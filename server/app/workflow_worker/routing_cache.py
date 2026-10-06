"""Cached route-row half of dispatch routing (split from ``routing``, file budget).

``try_claim_and_submit`` runs once per ready candidate per poll pass; with
tens of thousands of ready candidates and saturated local capacity, the
per-candidate route/binding SQL queries dominated the whole pass. The
route-row outcome (``node_route_decision.decide_routed`` + the code-pool
node limit) is therefore cached per (workspace, workflow, node) for
``ROUTE_CACHE_TTL_SECONDS``; the per-snapshot branches stay uncached in
``routing.resolve_node_route``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.jobs.queries.workspace_node_limits import get_local_node_limit
from server.app.services.agent_node_profile_types import PROFILE_SOURCE_DEFINITION
from server.app.services.node_route_decision import CatalogLoader, decide_routed

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


def cached_routed(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
    catalog: CatalogLoader,
) -> NodeRoute:
    key = (workspace_id, workflow_key, node.key)
    now = time.monotonic()
    cached = worker.state.route_cache.get(key)
    if cached is not None and now - cached[0] < ROUTE_CACHE_TTL_SECONDS:
        return cached[1]
    route = _resolve_uncached(worker, workspace_id, workflow_key, node, catalog)
    worker.state.route_cache[key] = (now, route)
    return route


def _resolve_uncached(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
    catalog: CatalogLoader,
) -> NodeRoute:
    # #211 Phase 3 (read-layer binding): the route predicate keys on
    # (workspace_id, node_key) — workflow_key equals the workspace id on
    # every row (v62 binding, aligned by v68). The cache key keeps the
    # workflow_key component until Phase 4 (frozen snapshots may still carry
    # a pre-v62 key, so the composite key stays collision-free).
    with worker.job_db._connect_read() as conn:
        row = conn.execute(
            """
            select target_kind, target_id from workspace_node_routes
            where workspace_id=%s and node_key=%s
            """,
            (workspace_id, node.key),
        ).fetchone()
        routed = decide_routed(
            node, dict(row) if row is not None else None, workspace_id=workspace_id, catalog=catalog
        )
        if routed.kind == "agent" and worker.agent_dispatch is None:
            raise RuntimeError("Agent dispatch service is not configured")
        if routed.kind != "executor":
            return NodeRoute(
                routed.kind, target_id=routed.target_id, error_message=routed.error_message
            )
        # Every non-Agent-routed node joins the implicit code pool (P-0.5):
        # no executor binding/allocation exists anymore; runnability is
        # enforced by node-code resolution at dispatch (EXEC-CODE-002).
        return NodeRoute(
            "executor",
            target_id=CODE_EXECUTOR_ID,
            local_node_limit=get_local_node_limit(conn, workspace_id, workflow_key, node.key),
        )
