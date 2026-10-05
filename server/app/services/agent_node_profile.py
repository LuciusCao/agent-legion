"""Agent node execution profile: the single read model for agent nodes (#932, #440 P1).

Every reader that needs an agent node's execution configuration (runtime,
tools, labels, config schema, legacy skill fallback) resolves it here
instead of walking the published Agent catalog itself. In P1 the only
``source`` is ``agent_definition``: the node's capability resolves to
exactly one published Agent of the workspace (the catalog's partial unique
index guarantees at most one per capability), so the profile is a
field-for-field projection of that definition — zero behavior change.
P2 (#933) adds ``source='node'`` for self-contained nodes; callers stay
unchanged because they read only the profile.

This module is pure (no DB): the legacy catalog is passed in. The loaders —
the only place allowed to read ``published_agent_definitions`` (ratchet,
``config/architecture/agent-definition-catalog-callers.json``) — live in
``agent_node_profile_catalog``; the split keeps ``node_config`` importable
from the Agent publish path without an import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from server.app.agent_catalog import AgentDefinition

AgentProfileSource = Literal["agent_definition"]


class AgentNodeLike(Protocol):
    """The node fields profile resolution reads (WorkflowNode satisfies it)."""

    @property
    def capability(self) -> str: ...

    @property
    def node_type(self) -> str: ...


@dataclass(frozen=True)
class LegacyAgentRef:
    """The published Agent definition a legacy-sourced profile projects."""

    agent_id: str
    definition: AgentDefinition

    @property
    def capability(self) -> str:
        return self.definition.capability

    def definition_hash(self) -> str:
        return self.definition.definition_hash()


@dataclass(frozen=True)
class AgentNodeProfile:
    """Execution profile of one agent node.

    ``skill`` is the legacy definition-level fallback (``""`` = none); the
    node's own ``skill`` binding still wins at dispatch (#76).
    """

    runtime: str
    tools: tuple[str, ...]
    requires_labels: Mapping[str, str]
    config_schema: Mapping[str, Any]
    skill: str
    source: AgentProfileSource
    legacy_ref: LegacyAgentRef | None


def profile_from_definition(agent_id: str, definition: AgentDefinition) -> AgentNodeProfile:
    """Project one published (or pinned) Agent definition into a profile."""
    return AgentNodeProfile(
        runtime=definition.runtime,
        tools=definition.tools,
        requires_labels=definition.requires_labels,
        config_schema=definition.config_schema,
        skill=definition.skill,
        source="agent_definition",
        legacy_ref=LegacyAgentRef(agent_id=agent_id, definition=definition),
    )


CapabilityIndex = Mapping[str, tuple[str, ...]]


def build_capability_index(legacy_catalog: Mapping[str, AgentDefinition]) -> CapabilityIndex:
    """capability → agent ids serving it (catalog order), built in one pass.

    Callers resolving many nodes against one catalog build this once and
    pass it as ``index=`` so a workflow resolves in O(agents + nodes)
    (PR #987 codex R2).
    """
    index: dict[str, list[str]] = {}
    for agent_id, definition in legacy_catalog.items():
        index.setdefault(definition.capability, []).append(agent_id)
    return {capability: tuple(ids) for capability, ids in index.items()}


def legacy_agent_candidates(
    node: AgentNodeLike,
    legacy_catalog: Mapping[str, AgentDefinition],
    *,
    index: CapabilityIndex | None = None,
) -> tuple[str, ...]:
    """Agent ids in *legacy_catalog* serving the node's capability (catalog order).

    Callers that must tell "none" from "ambiguous" (route materialization,
    publish diagnostics) read the count; everyone else uses
    ``resolve_agent_node_profile``. *index* (``build_capability_index`` of
    the same catalog) replaces the per-call catalog scan.
    """
    if index is not None:
        return index.get(node.capability, ())
    return tuple(
        agent_id
        for agent_id, definition in legacy_catalog.items()
        if definition.capability == node.capability
    )


def resolve_agent_node_profile(
    node: AgentNodeLike,
    legacy_catalog: Mapping[str, AgentDefinition],
    *,
    index: CapabilityIndex | None = None,
) -> AgentNodeProfile | None:
    """The node's execution profile, or None when it has none.

    None for non-agent nodes (#284: only ``type: agent`` dispatches through
    an Agent) and when the capability resolves to zero or several published
    Agents — the latter is a catalog error every caller already rejects or
    treats as unresolved (publish gate, route derivation). Pass *index*
    when resolving several nodes against the same catalog.
    """
    if node.node_type != "agent":
        return None
    candidates = legacy_agent_candidates(node, legacy_catalog, index=index)
    if len(candidates) != 1:
        return None
    agent_id = candidates[0]
    return profile_from_definition(agent_id, legacy_catalog[agent_id])


def resolve_routed_agent_profile(
    agent_id: str, legacy_catalog: Mapping[str, AgentDefinition]
) -> AgentNodeProfile | None:
    """Profile of the Agent a materialized route (``workspace_node_routes``) targets."""
    definition = legacy_catalog.get(agent_id)
    return profile_from_definition(agent_id, definition) if definition is not None else None
