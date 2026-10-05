"""Legacy agent route fallback for jobs frozen before a profile switch (#933).

Split from ``routing`` (file budget). When the active revision turns a node
self-contained, revision publish stops materializing its
``workspace_node_routes`` row — but in-flight jobs whose frozen snapshot
still has the legacy (definition-sourced) node must keep dispatching
through their Agent definition, never fall into the code pool (PR #1039
codex R3). The fallback resolves the node's capability against the
workspace's published catalog, exactly like revision publish derives the
route; enqueue re-validates the definition hash (``_validate_agent_route``
accepts a missing route row, but never a row pointing elsewhere).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.agent_node_profile import resolve_agent_node_profile
from server.app.services.agent_node_profile_catalog import legacy_agent_catalog

if TYPE_CHECKING:
    from server.app.workflow_worker.routing import NodeRoute
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.schema import WorkflowNode


def legacy_agent_fallback_route(
    worker: WorkflowWorkerThread, workspace_id: str, node: WorkflowNode
) -> NodeRoute:
    """Agent route for a route-less legacy agent node, or a config error."""
    from server.app.workflow_worker.routing import NodeRoute

    profile = resolve_agent_node_profile(node, legacy_agent_catalog(worker.job_db, workspace_id))
    if profile is None or profile.legacy_ref is None:
        return NodeRoute(
            "error",
            error_message=(
                f"agent node {node.key!r} has no Agent route and capability"
                f" {node.capability!r} does not resolve to exactly one published Agent;"
                " if the active revision made the node self-contained (execution.runtime),"
                " upgrade the job to the active revision"
            ),
        )
    if worker.agent_dispatch is None:
        raise RuntimeError("Agent dispatch service is not configured")
    return NodeRoute("agent", target_id=profile.legacy_ref.agent_id)
