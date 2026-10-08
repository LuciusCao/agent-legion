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
from server.app.workflows import workflow_node_profile as frozen

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


def route_cache_key(
    workspace_id: str, workflow_key: str, node: WorkflowNode
) -> tuple[str, str, str, bool]:
    """Route cache key of a frozen snapshot node (#935 R1, #1091).

    The snapshot's agent-ness is part of the key: a route row may outlive a
    node one job froze as ``code`` while another job froze it as an agent
    node, and the two must never share a cached route.
    """
    is_agent = frozen.frozen_node_execution_kind(node) != frozen.FROZEN_NODE_CODE
    return (workspace_id, workflow_key, node.key, is_agent)


def resolve_node_route(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
) -> NodeRoute:
    """Resolve a node's route, through the worker's short-TTL cache.

    *node* comes from the job's frozen snapshot and its frozen execution kind
    decides first (#1091, ``frozen_node_execution_kind`` — shared with the
    job detail projection): a code node always joins the code pool whatever
    route row a later revision materialized; a legacy agent node goes through
    its route row (or the capability fallback once the row is pruned). A
    self-contained agent node (#933, ``execution.runtime``) routes to itself
    with zero DB and is never cached: the cache key is per (workspace,
    workflow, node, agent-ness) while the profile is per job snapshot, so a
    legacy job and an upgraded job sharing a node key must not see each
    other's route.
    """
    kind = frozen.frozen_node_execution_kind(node)
    if kind == frozen.FROZEN_NODE_SELF_CONTAINED_AGENT:
        if worker.agent_dispatch is None:
            raise RuntimeError("Agent dispatch service is not configured")
        return NodeRoute("agent", target_id=node.key, profile_source=PROFILE_SOURCE_NODE)
    is_agent = kind == frozen.FROZEN_NODE_LEGACY_AGENT
    key = route_cache_key(workspace_id, workflow_key, node)
    now = time.monotonic()
    cached = worker.state.route_cache.get(key)
    if cached is not None and now - cached[0] < ROUTE_CACHE_TTL_SECONDS:
        route = cached[1]
    else:
        route = _resolve_uncached(worker, workspace_id, workflow_key, node, is_agent=is_agent)
        worker.state.route_cache[key] = (now, route)
    if route.kind == "executor" and is_agent:
        # A legacy (non-self-contained) agent node with no route row: its
        # job froze an older revision, and the active one has since made the
        # node self-contained or ``code`` (row pruned, #933 / #935 R1). Never
        # send an agent node to the code pool — resolve the legacy profile
        # from its capability (uncached: per-snapshot, like the self-contained
        # path).
        return legacy_agent_fallback_route(worker, workspace_id, node)
    return route


def _resolve_uncached(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
    *,
    is_agent: bool,
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
            (workspace_id, node.key),
        ).fetchone()
        # Agent routing is decided by the materialized workspace_node_routes
        # projection — but only for nodes the job snapshot declares ``agent``
        # (#935 R1): a row left behind for a node since turned ``code`` must
        # never pull it off the code pool.
        if is_agent and route is not None and route["target_kind"] == "agent":
            agent_id = str(route["target_id"])
            profile = resolve_dispatch_agent_profile(worker.job_db, workspace_id, agent_id, None)
            if profile is None or profile.legacy_ref is None:
                return NodeRoute(
                    "error",
                    error_message=(
                        f"Agent {agent_id!r} has no published definition in workspace"
                        f" {workspace_id!r}; agent definitions are workspace-scoped"
                        " (schema v46) — upgrade the job to the active revision, whose agent"
                        " nodes carry their own execution profile"
                    ),
                )
            if profile.legacy_ref.capability != node.capability:
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
            local_node_limit=get_local_node_limit(conn, workspace_id, workflow_key, node.key),
        )
