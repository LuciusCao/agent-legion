"""Agent node profile value types and projections (#932, #933).

Split from ``agent_node_profile`` (file budget): the profile dataclasses,
the ``profile_source`` constants (schema v92 request column values), and
the two projections — published/pinned Agent definition → profile
(``source='agent_definition'``) and self-contained node → profile
(``source='node'``). ``agent_node_profile`` re-exports every name here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog.definition import DEFAULT_TOOLS
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

AgentProfileSource = Literal["agent_definition", "node"]

#: ``agent_execution_requests.profile_source`` values (schema v92).
PROFILE_SOURCE_DEFINITION: AgentProfileSource = "agent_definition"
PROFILE_SOURCE_NODE: AgentProfileSource = "node"


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
    #: The definition dispatch freezes into the manifest: the published one
    #: (legacy) or the node profile's projection (``source='node'``).
    dispatch_definition: AgentDefinition

    def identity_hash(self) -> str:
        """Implementation identity: definition hash (legacy) or profile hash (node).

        Lands in ``agent_execution_requests.agent_definition_hash`` and the
        node_runs mirror (#645 identity chain) for both sources.
        """
        return self.dispatch_definition.definition_hash()


def profile_from_definition(agent_id: str, definition: AgentDefinition) -> AgentNodeProfile:
    """Project one published (or pinned) Agent definition into a profile."""
    return AgentNodeProfile(
        runtime=definition.runtime,
        tools=definition.tools,
        requires_labels=definition.requires_labels,
        config_schema=definition.config_schema,
        skill=definition.skill,
        source=PROFILE_SOURCE_DEFINITION,
        legacy_ref=LegacyAgentRef(agent_id=agent_id, definition=definition),
        dispatch_definition=definition,
    )


def profile_from_node(node: Any) -> AgentNodeProfile:
    """Project a self-contained agent node into its profile (``source='node'``).

    Undeclared ``tools`` fall back to the same default set a fresh Agent
    definition gets; ``skill`` stays empty (the node's own binding is the
    only source). The projection doubles as the dispatch definition, so its
    ``definition_hash()`` is the profile hash the request row records.
    """
    definition = AgentDefinition(
        capability=node.capability,
        runtime=node.execution.runtime,
        tools=tuple(node.tools) or DEFAULT_TOOLS,
        requires_labels=dict(node.requires_labels),
        config_schema=dict(node.config_schema),
    )
    return AgentNodeProfile(
        runtime=definition.runtime,
        tools=definition.tools,
        requires_labels=definition.requires_labels,
        config_schema=definition.config_schema,
        skill="",
        source=PROFILE_SOURCE_NODE,
        legacy_ref=None,
        dispatch_definition=definition,
    )


def node_profile_error(node: Any) -> str | None:
    """Publish-time half-filled profile check (#933): labels need a runtime.

    A node declaring ``requires_labels`` without ``execution.runtime`` would
    mix node-declared labels with a definition-sourced profile; reject it.
    """
    if getattr(node, "node_type", "") != "agent" or is_self_contained_agent_node(node):
        return None
    if getattr(node, "requires_labels", None):
        return (
            f"Agent node {node.key} declares requires_labels but no execution.runtime:"
            " a self-contained profile needs execution.runtime (or a workflow top-level"
            " execution.runtime default); remove requires_labels to keep the Agent definition"
        )
    return None
