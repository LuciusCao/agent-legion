"""Legacy Agent-catalog loaders behind the agent node profile facade (#932).

The only production module allowed to read the published Agent catalog
(``published_agent_definitions`` / ``has_published_agent_definitions``):
the ratchet in ``scripts/architecture/agent_definition_callers.py`` turns
any new direct caller red. Every other reader loads the legacy catalog here
and resolves node profiles with ``agent_node_profile``, which since P2
(#933) also yields self-contained ``source='node'`` profiles without
touching those readers again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.agent_profile_scan import has_self_contained_agent_nodes
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

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def legacy_agent_catalog(
    connect_source: ConnectSource, workspace_id: str
) -> Mapping[str, AgentDefinition]:
    """The workspace's published Agent definitions keyed by agent_id (~5s cache, hot paths)."""
    return published_agent_definitions(connect_source, workspace_id)


def fresh_legacy_agent_catalog(
    job_db: JobQueries, workspace_id: str
) -> Mapping[str, AgentDefinition]:
    """Uncached published catalog via the JobQueries facade (BOUNDARY-DATA-001).

    For callers that need post-publish truth rather than the process cache
    (workflow upgrade identity checks, #645 P1-1).
    """
    return {
        agent_id: AgentDefinition.model_validate(document)
        for agent_id, document in job_db.published_agent_definition_documents(workspace_id).items()
    }


def agent_profiles_may_exist(connect_source: ConnectSource) -> bool:
    """Cheap cross-workspace probe for poll-loop scan gates (never for resolution).

    True when any workspace has a published Agent (legacy source) OR any
    revision a job may still dispatch from (the active one, or one a runnable
    job is pinned to) has a self-contained agent node (#933). The second half
    is load-bearing: a workspace with no Agent definitions at all would
    otherwise never scan its agent candidates (thread / agent_gate gates).
    """
    return has_published_agent_definitions(connect_source) or has_self_contained_agent_nodes(
        connect_source
    )


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
