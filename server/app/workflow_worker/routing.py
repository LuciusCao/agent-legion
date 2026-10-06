"""Node routing resolution cache for the workflow worker's claim path.

``try_claim_and_submit`` runs once per ready candidate per poll pass; with
tens of thousands of ready candidates and saturated local capacity, the
per-candidate route/binding SQL queries dominated the whole pass. Routing
configuration changes rarely (operator edits in Workspace settings), so the
resolution is cached for a short TTL: a stale entry at worst claims against
a seconds-old binding or fails a claim that the next pass retries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.services.node_route_decision import RouteDecision, decide_node_route
from server.app.workflow_worker.routing_cache import (
    ROUTE_CACHE_TTL_SECONDS as ROUTE_CACHE_TTL_SECONDS,
)
from server.app.workflow_worker.routing_cache import NodeRoute as NodeRoute
from server.app.workflow_worker.routing_cache import cached_routed
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.schema import WorkflowNode


def resolve_node_route(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
) -> NodeRoute:
    """Resolve a node's route: cached DB reads + the shared pure decision.

    The decision lives in ``services/node_route_decision`` (single source
    shared with the job-detail projection, PR #1085). The route-row half
    (``decide_routed``) is cached per (workspace, workflow, node) for a
    short TTL; the per-snapshot branches — a self-contained node (#933,
    zero DB) and the capability fallback of a route-less legacy agent node —
    are never cached: the cache key is per node key while those depend on
    the job's frozen node, so jobs frozen on different revisions sharing a
    node key must not see each other's outcome.
    """
    catalog = lambda: legacy_agent_catalog(worker.job_db, workspace_id)  # noqa: E731
    cached: NodeRoute | None = None
    routed: RouteDecision | None = None
    if not is_self_contained_agent_node(node):
        cached = cached_routed(worker, workspace_id, workflow_key, node, catalog)
        routed = RouteDecision(
            cached.kind,  # type: ignore[arg-type]
            target_id=cached.target_id,
            error_message=cached.error_message,
            profile_source=cached.profile_source,
        )
    decision = decide_node_route(
        node, None, workspace_id=workspace_id, catalog=catalog, routed=routed
    )
    if decision.kind == "agent" and worker.agent_dispatch is None:
        raise RuntimeError("Agent dispatch service is not configured")
    if cached is not None and decision == routed:
        return cached  # keeps the cached code-pool node limit
    return NodeRoute(
        decision.kind,
        target_id=decision.target_id,
        error_message=decision.error_message,
        profile_source=decision.profile_source,
    )
