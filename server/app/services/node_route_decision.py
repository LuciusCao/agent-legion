"""Single source of truth for "where does this node dispatch?" (#933, PR #1085).

Pure decision, no DB and no worker state: inputs are the job's frozen node
definition, the node's current ``workspace_node_routes`` row (or None), and
a lazy loader for the workspace's published Agent catalog. Both consumers
call it — the workflow worker's dispatch routing
(``workflow_worker/routing.resolve_node_route``) and the job-detail
projection (``job_node_executor_projection``) — so the detail always shows
what dispatch actually does.

Branches (in order):

1. self-contained agent node (``execution.runtime``) → itself
   (``profile_source='node'``), whatever routes exist;
2. current route row to an Agent → that Agent, validated against the
   published catalog (missing definition / capability mismatch → error);
3. no Agent route, legacy ``type: agent`` node (its job froze a revision
   from before the node became self-contained) → capability fallback to the
   one published Agent, else error;
4. otherwise → the implicit code pool.

#1091: dispatch does not yet honour the frozen node type when a later
revision routes a frozen ``code`` node to an Agent (branch 2 wins). Making
branch 2 conditional on ``node.node_type == 'agent'`` here switches dispatch
and the job detail to frozen-type-first together.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile import (
    resolve_agent_node_profile,
    resolve_routed_agent_profile,
)
from server.app.services.agent_node_profile_types import (
    PROFILE_SOURCE_DEFINITION,
    PROFILE_SOURCE_NODE,
)
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

RouteKind = Literal["agent", "executor", "error"]
CatalogLoader = Callable[[], Mapping[str, AgentDefinition]]


@dataclass(frozen=True)
class RouteDecision:
    """Dispatch decision for one node of one job snapshot."""

    kind: RouteKind
    target_id: str = ""
    error_message: str = ""
    profile_source: str = PROFILE_SOURCE_DEFINITION


def decide_routed(
    node: Any,
    route_row: Mapping[str, Any] | None,
    *,
    workspace_id: str,
    catalog: CatalogLoader,
) -> RouteDecision:
    """Branches 2 and 4: the outcome the current route row alone implies.

    Depends only on the node key's route row (plus the node capability), so
    dispatch may cache it per node key; ``decide_node_route`` layers the
    per-snapshot branches (1 and 3) on top.
    """
    if route_row is not None and route_row.get("target_kind") == "agent":
        agent_id = str(route_row["target_id"])
        profile = resolve_routed_agent_profile(agent_id, catalog())
        if profile is None or profile.legacy_ref is None:
            return RouteDecision(
                "error",
                error_message=(
                    f"Agent {agent_id!r} has no published definition in workspace"
                    f" {workspace_id!r}; agent definitions are workspace-scoped"
                    " (schema v46) — publish one in Studio (Agent 管理) for this workspace"
                ),
            )
        if profile.legacy_ref.capability != node.capability:
            return RouteDecision("error", error_message=f"Invalid Agent route {agent_id!r}")
        return RouteDecision("agent", target_id=agent_id)
    return RouteDecision("executor")


def decide_node_route(
    node: Any,
    route_row: Mapping[str, Any] | None,
    *,
    workspace_id: str,
    catalog: CatalogLoader,
    routed: RouteDecision | None = None,
) -> RouteDecision:
    """Decide where *node* dispatches; *catalog* is read only when needed.

    *routed* lets a caller pass a cached ``decide_routed`` result for the
    node key instead of re-deriving it from *route_row*.
    """
    if is_self_contained_agent_node(node):
        return RouteDecision("agent", target_id=node.key, profile_source=PROFILE_SOURCE_NODE)
    if routed is None:
        routed = decide_routed(node, route_row, workspace_id=workspace_id, catalog=catalog)
    if routed.kind != "executor" or node.node_type != "agent":
        return routed
    fallback = resolve_agent_node_profile(node, catalog())
    if fallback is None or fallback.legacy_ref is None:
        return RouteDecision(
            "error",
            error_message=(
                f"agent node {node.key!r} has no Agent route and capability"
                f" {node.capability!r} does not resolve to exactly one published Agent;"
                " if the active revision made the node self-contained (execution.runtime),"
                " upgrade the job to the active revision"
            ),
        )
    return RouteDecision("agent", target_id=fallback.legacy_ref.agent_id)
