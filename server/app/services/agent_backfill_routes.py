"""Active-revision route resolution for the Agent backfill dry-run (#934 codex R1).

Dispatch runs an active-revision agent node through its materialized route
(``workspace_node_routes``), and Agent publish / archive never rewrites
routes — so the route target, not today's capability lookup, is what the
node executes and what the 0.7.17 backfill must copy from. The target's
definition is read even when archived. Whenever the route target and the
catalog's capability resolution disagree the node is flagged
``route_drift``. Drafts keep capability resolution (what the next publish
would route) and never come through here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile import (
    AgentNodeProfile,
    profile_from_definition,
    resolve_routed_agent_profile,
)
from server.app.services.agent_service import AgentService

if TYPE_CHECKING:
    from server.app.jobs.queries.agent_backfill_reader import AgentBackfillReader


@dataclass(frozen=True)
class RouteTarget:
    """A node's materialized route target and its definition (any status).

    ``status``: ``published`` / ``archived`` / ``draft`` (latest version) or
    ``missing`` (no version left); ``definition`` is None only when missing.
    """

    agent_id: str
    status: str
    definition: AgentDefinition | None


def load_route_targets(
    reader: AgentBackfillReader, workspace_id: str, catalog: Mapping[str, AgentDefinition]
) -> dict[str, RouteTarget]:
    """node_key → materialized route target, its definition read in any status.

    A published target projects from the catalog; otherwise the newest
    archived version (the one dispatch last ran) is preferred over a newer
    unpublished draft, so the report shows what the route actually points at.
    """
    service = AgentService(reader, workspace_id)
    targets: dict[str, RouteTarget] = {}
    for node_key, agent_id in reader.agent_route_targets(workspace_id).items():
        if agent_id in catalog:
            targets[node_key] = RouteTarget(agent_id, "published", catalog[agent_id])
            continue
        versions = service.list_versions(agent_id)
        chosen = next((v for v in versions if v.status == "archived"), None) or next(
            iter(versions), None
        )
        targets[node_key] = (
            RouteTarget(agent_id, "missing", None)
            if chosen is None
            else RouteTarget(
                agent_id, chosen.status, AgentDefinition.model_validate(chosen.definition)
            )
        )
    return targets


def _routed_profile(
    target: RouteTarget, catalog: Mapping[str, AgentDefinition]
) -> AgentNodeProfile | None:
    if target.status == "published":
        return resolve_routed_agent_profile(target.agent_id, catalog)
    if target.definition is None:
        return None
    return profile_from_definition(target.agent_id, target.definition)


def resolve_routed_node(
    node_key: str,
    routes: Mapping[str, RouteTarget],
    candidates: Sequence[str],
    catalog: Mapping[str, AgentDefinition],
) -> tuple[dict[str, Any], AgentNodeProfile | None, tuple[str, list[str]] | None]:
    """(route fields, backfill profile, unresolved (reason, ids) or None).

    *candidates*: the catalog's capability resolution for the node. No route
    and no candidate returns (fields, None, None): the caller classifies it
    like any capability miss.
    """
    target = routes.get(node_key)
    unique = candidates[0] if len(candidates) == 1 else None
    fields: dict[str, Any] = {
        "route": {"target_id": target.agent_id, "target_status": target.status} if target else None
    }
    if (target.agent_id if target else None) != unique:
        fields["route_drift"] = {"catalog_candidates": sorted(candidates)}
    if target is None:
        return fields, None, (("no_route", sorted(candidates)) if candidates else None)
    profile = _routed_profile(target, catalog)
    if profile is None:
        return fields, None, ("route_target_missing", [target.agent_id])
    return fields, profile, None
