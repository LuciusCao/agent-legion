"""Node routing resolution cache for the workflow worker's claim path.

``try_claim_and_submit`` runs once per ready candidate per poll pass; with
tens of thousands of ready candidates and saturated local capacity, the
per-candidate route/binding SQL queries dominated the whole pass. Routing
configuration changes rarely (operator edits in Workspace settings), so the
resolution is cached for a short TTL: a stale entry at worst claims against
a seconds-old binding or fails a claim that the next pass retries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.jobs.queries.workspace_node_limits import get_local_node_limit
from server.app.services.agent_service import published_agent_definitions

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

ROUTE_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class NodeRoute:
    """Resolved routing outcome for one (workspace, workflow, node)."""

    kind: str  # "agent" | "executor" | "error"
    target_id: str = ""
    local_node_limit: int | None = None
    error_message: str = ""


def resolve_node_route(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    capability: str,
) -> NodeRoute:
    """Resolve a node's route, through the worker's short-TTL cache."""
    key = (workspace_id, workflow_key, node_key)
    now = time.monotonic()
    cached = worker._route_cache.get(key)
    if cached is not None and now - cached[0] < ROUTE_CACHE_TTL_SECONDS:
        return cached[1]
    route = _resolve_uncached(worker, workspace_id, workflow_key, node_key, capability)
    worker._route_cache[key] = (now, route)
    return route


def _resolve_uncached(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    capability: str,
) -> NodeRoute:
    with worker.job_db._connect_read() as conn:
        route = conn.execute(
            """
            select target_kind, target_id from workspace_node_routes
            where workspace_id=%s and workflow_key=%s and node_key=%s
            """,
            (workspace_id, workflow_key, node_key),
        ).fetchone()
        # Agent routing is decided by the materialized workspace_node_routes
        # projection, not by any node-level declaration.
        if route is not None and route["target_kind"] == "agent":
            agent_id = str(route["target_id"])
            definition_config = published_agent_definitions(
                worker.settings.database_url, workspace_id
            ).get(agent_id)
            if definition_config is None:
                return NodeRoute(
                    "error",
                    error_message=(
                        f"Agent {agent_id!r} has no published definition in workspace"
                        f" {workspace_id!r}; agent definitions are workspace-scoped"
                        " (schema v46) — create one in Studio (Agent 管理) for this workspace"
                    ),
                )
            if definition_config.capability != capability:
                return NodeRoute("error", error_message=f"Invalid Agent route {agent_id!r}")
            if worker.agent_dispatch is None:
                raise RuntimeError("Agent dispatch service is not configured")
            return NodeRoute("agent", target_id=agent_id)

        # Every non-Agent-routed node joins the implicit code pool (P-0.5):
        # no executor binding/allocation exists anymore; runnability is
        # enforced by node-code resolution at dispatch (EXEC-CODE-002).
        return NodeRoute(
            "executor",
            target_id=CODE_EXECUTOR_ID,
            local_node_limit=get_local_node_limit(conn, workspace_id, workflow_key, node_key),
        )
