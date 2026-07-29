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

from server.app.jobs.queries.workspace_node_bindings import (
    get_binding,
    get_local_node_limit,
    has_local_node_limit,
)

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
            definition_config = worker.settings.agent_definitions.get(agent_id)
            if definition_config is None or definition_config.capability != capability:
                return NodeRoute("error", error_message=f"Invalid Agent route {agent_id!r}")
            if worker.agent_dispatch is None:
                raise RuntimeError("Agent dispatch service is not configured")
            return NodeRoute("agent", target_id=agent_id)

        binding = get_binding(conn, workspace_id, workflow_key, node_key)
        if binding is None:
            return NodeRoute("error", error_message="No Executor binding")
        executor_id = str(binding["executor_id"])
        try:
            executor = worker.registry.require(executor_id, capability)
        except Exception as exc:
            return NodeRoute("error", error_message=str(exc))
        if executor.kind == "code":
            return NodeRoute(
                "executor",
                target_id=executor_id,
                local_node_limit=get_local_node_limit(conn, workspace_id, workflow_key, node_key),
            )
        if has_local_node_limit(conn, workspace_id, workflow_key, node_key):
            return NodeRoute(
                "error",
                error_message="Node limits are not supported for agent executors",
            )
        return NodeRoute("executor", target_id=executor_id)
