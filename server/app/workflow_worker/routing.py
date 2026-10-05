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
from server.app.services.agent_node_profile_catalog import resolve_dispatch_agent_profile
from server.app.services.agent_node_profile_types import (
    PROFILE_SOURCE_DEFINITION,
    PROFILE_SOURCE_NODE,
)
from server.app.workflow_worker.routing_fallback import legacy_agent_fallback_route
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.schema import WorkflowNode

ROUTE_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class NodeRoute:
    """Resolved routing outcome for one (workspace, workflow, node)."""

    kind: str  # "agent" | "executor" | "error"
    target_id: str = ""
    local_node_limit: int | None = None
    error_message: str = ""
    # #933: 'node' = self-contained agent node (target_id is the node key,
    # the profile comes from the job snapshot); 'agent_definition' = routed
    # to a published Agent through workspace_node_routes.
    profile_source: str = PROFILE_SOURCE_DEFINITION


def resolve_node_route(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
) -> NodeRoute:
    """Resolve a node's route, through the worker's short-TTL cache.

    A self-contained agent node (#933, ``execution.runtime`` in the job's
    frozen snapshot) routes to itself with zero DB and is never cached: the
    cache key is per (workspace, workflow, node) while the profile is per
    job snapshot, so a legacy job and an upgraded job sharing a node key
    must not see each other's route.
    """
    if is_self_contained_agent_node(node):
        if worker.agent_dispatch is None:
            raise RuntimeError("Agent dispatch service is not configured")
        return NodeRoute("agent", target_id=node.key, profile_source=PROFILE_SOURCE_NODE)
    node_key = node.key
    capability = node.capability
    key = (workspace_id, workflow_key, node_key)
    now = time.monotonic()
    cached = worker.state.route_cache.get(key)
    if cached is not None and now - cached[0] < ROUTE_CACHE_TTL_SECONDS:
        route = cached[1]
    else:
        route = _resolve_uncached(worker, workspace_id, workflow_key, node_key, capability)
        worker.state.route_cache[key] = (now, route)
    if route.kind == "executor" and node.node_type == "agent":
        # A legacy (non-self-contained) agent node with no route row: its
        # job froze an older revision, and the active one has since made the
        # node self-contained (no route materialized, #933). Never send an
        # agent node to the code pool — resolve the legacy profile from its
        # capability (uncached: per-snapshot, like the self-contained path).
        return legacy_agent_fallback_route(worker, workspace_id, node)
    return route


def _resolve_uncached(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    capability: str,
) -> NodeRoute:
    # #211 Phase 3 (read-layer binding): the route predicate keys on
    # (workspace_id, node_key) — workflow_key equals the workspace id on
    # every row (v62 binding, aligned by v68). The cache key keeps the
    # workflow_key component until Phase 4 (frozen snapshots may still carry
    # a pre-v62 key, so the composite key stays collision-free).
    with worker.job_db._connect_read() as conn:
        route = conn.execute(
            """
            select target_kind, target_id from workspace_node_routes
            where workspace_id=%s and node_key=%s
            """,
            (workspace_id, node_key),
        ).fetchone()
        # Agent routing is decided by the materialized workspace_node_routes
        # projection, not by any node-level declaration.
        if route is not None and route["target_kind"] == "agent":
            agent_id = str(route["target_id"])
            profile = resolve_dispatch_agent_profile(worker.job_db, workspace_id, agent_id, None)
            if profile is None or profile.legacy_ref is None:
                return NodeRoute(
                    "error",
                    error_message=(
                        f"Agent {agent_id!r} has no published definition in workspace"
                        f" {workspace_id!r}; agent definitions are workspace-scoped"
                        " (schema v46) — create one in Studio (Agent 管理) for this workspace"
                    ),
                )
            if profile.legacy_ref.capability != capability:
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
