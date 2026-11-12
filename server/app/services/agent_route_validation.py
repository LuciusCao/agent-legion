"""Shared legacy Agent route adjudication for dispatch and job detail (#1128).

A legacy agent node (snapshot frozen before the v93 backfill) dispatches
through its frozen ``workspace_node_routes`` row, or — once a later revision
pruned the row — through the capability fallback. Both resolutions are only
valid while the target Agent still resolves: dispatch
(``workflow_worker.routing``) fails the node as a configuration error when
the row points at an unpublished Agent or one whose capability drifted. The
job detail projection reuses the same adjudication here so ``GET /jobs/{id}``
and Studio MCP fill ``route_error`` with dispatch's exact message instead of
projecting a stale Agent identity.
"""

from __future__ import annotations

from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services.agent_node_profile_catalog import (
    legacy_fallback_agent_id,
    resolve_dispatch_agent_profile,
)


def routed_agent_error(
    connect_source: ConnectSource, workspace_id: str, agent_id: str, capability: str
) -> str | None:
    """Why dispatch would reject a frozen route row to *agent_id*, or None."""
    profile = resolve_dispatch_agent_profile(connect_source, workspace_id, agent_id, None)
    if profile is None or profile.legacy_ref is None:
        return (
            f"Agent {agent_id!r} has no published definition in workspace"
            f" {workspace_id!r}; agent definitions are workspace-scoped"
            " (schema v46) — upgrade the job to the active revision, whose agent"
            " nodes carry their own execution profile"
        )
    if profile.legacy_ref.capability != capability:
        return f"Invalid Agent route {agent_id!r}"
    return None


def legacy_fallback_error(node: Any) -> str:
    """Dispatch's configuration error for a route-less legacy node the capability fallback misses."""
    return (
        f"agent node {node.key!r} has no Agent route and capability"
        f" {node.capability!r} does not resolve to exactly one published Agent;"
        " if the active revision made the node self-contained (execution.runtime),"
        " upgrade the job to the active revision"
    )


def legacy_agent_route(
    connect_source: ConnectSource,
    workspace_id: str,
    node: Any,
    routed_agent_id: str | None,
) -> tuple[str | None, str | None]:
    """(agent_id, route_error) for a legacy agent node — dispatch's adjudication.

    Exactly one element is non-None: the Agent the node dispatches to, or the
    configuration error dispatch would fail the node with.
    """
    if routed_agent_id is not None:
        error = routed_agent_error(connect_source, workspace_id, routed_agent_id, node.capability)
        return (routed_agent_id, None) if error is None else (None, error)
    agent_id = legacy_fallback_agent_id(connect_source, workspace_id, node)
    return (agent_id, None) if agent_id is not None else (None, legacy_fallback_error(node))
