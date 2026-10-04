"""Legacy Agent-catalog loaders behind the agent node profile facade (#932).

The only production module allowed to read the published Agent catalog
(``published_agent_definitions`` / ``has_published_agent_definitions``):
the ratchet in ``scripts/architecture/agent_definition_callers.py`` turns
any new direct caller red. Every other reader loads the legacy catalog here
and resolves node profiles with ``agent_node_profile``; P2 (#933) widens
the profile sources without touching those readers again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.services.agent_node_profile import (
    AgentNodeProfile,
    profile_from_definition,
    resolve_routed_agent_profile,
)
from server.app.services.agent_service import (
    has_published_agent_definitions,
    published_agent_definitions,
)
from server.app.services.agent_version_pins import resolve_pinned_agent_definition
from server.app.services.versioned_entities import VersionedEntityStore


def legacy_agent_catalog(
    connect_source: ConnectSource, workspace_id: str, *, cached: bool = True
) -> Mapping[str, AgentDefinition]:
    """The workspace's published Agent definitions keyed by agent_id.

    ``cached=True`` reads through the ~5s ``published_agent_definitions``
    cache (hot paths). ``cached=False`` reads the store directly for callers
    that need post-publish truth (upgrade identity checks, #645 P1-1).
    """
    if cached:
        return published_agent_definitions(connect_source, workspace_id)
    return {
        entity.entity_key: AgentDefinition.model_validate(entity.definition)
        for entity in VersionedEntityStore(connect_source, "agent").list_published(workspace_id)
    }


def agent_profiles_may_exist(connect_source: ConnectSource) -> bool:
    """Cheap cross-workspace probe for poll-loop scan gates (never for resolution)."""
    return has_published_agent_definitions(connect_source)


def resolve_dispatch_agent_profile(
    connect_source: ConnectSource,
    workspace_id: str,
    agent_id: str,
    pin: Mapping[str, Any] | None,
) -> AgentNodeProfile | None:
    """Profile for dispatching a route to *agent_id*; a frozen version pin wins.

    Strictly workspace-scoped (schema v46). None when the unpinned published
    definition is gone (the caller reports the invalid route); a pin whose
    agent, version, or definition hash no longer matches raises ValueError so
    the node fails closed (quality replay, schema v29).
    """
    if pin is None:
        return resolve_routed_agent_profile(
            agent_id, legacy_agent_catalog(connect_source, workspace_id)
        )
    definition = resolve_pinned_agent_definition(connect_source, workspace_id, agent_id, pin)
    return profile_from_definition(agent_id, definition)
