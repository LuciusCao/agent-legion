"""Agent node profile resolver (#932, #440 P1): pure projection of the legacy catalog."""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile import (
    legacy_agent_candidates,
    profile_from_definition,
    resolve_agent_node_profile,
    resolve_routed_agent_profile,
)
from server.app.workflows.definition import WorkflowNode

pytestmark = pytest.mark.no_db

_SCHEMA = {"type": "object", "properties": {"tone": {"type": "string", "default": "calm"}}}


def _definition(capability: str = "generate", **overrides) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"capability": capability, "runtime": "velites", **overrides}
    )


def _node(node_type: str = "agent", capability: str = "generate") -> WorkflowNode:
    return WorkflowNode(
        key="n1", label="n1", capability=capability, node_type=node_type, outputs=["o.json"]
    )


def test_profile_projects_every_definition_field() -> None:
    definition = _definition(
        runtime="pi",
        skill="group/skill",
        tools=("read",),
        requires_labels={"arch": "arm64"},
        config_schema=_SCHEMA,
    )

    profile = resolve_agent_node_profile(_node(), {"agent-1": definition})

    assert profile is not None
    assert profile.runtime == "pi"
    assert profile.tools == ("read",)
    assert dict(profile.requires_labels) == {"arch": "arm64"}
    assert dict(profile.config_schema) == _SCHEMA
    assert profile.skill == "group/skill"
    assert profile.source == "agent_definition"
    assert profile.legacy_ref is not None
    assert profile.legacy_ref.agent_id == "agent-1"
    assert profile.legacy_ref.capability == "generate"
    assert profile.legacy_ref.definition_hash() == definition.definition_hash()


def test_defaults_carry_over_unchanged() -> None:
    definition = _definition()

    profile = profile_from_definition("agent-1", definition)

    assert profile.tools == definition.tools
    assert profile.skill == ""
    assert dict(profile.config_schema) == {}
    assert dict(profile.requires_labels) == {}


@pytest.mark.parametrize("node_type", ["code", "start", "approval"])
def test_non_agent_nodes_have_no_profile(node_type: str) -> None:
    catalog = {"agent-1": _definition()}

    assert resolve_agent_node_profile(_node(node_type), catalog) is None


def test_unresolved_and_ambiguous_capabilities_have_no_profile() -> None:
    other = {"agent-1": _definition("other")}
    ambiguous = {"agent-1": _definition(), "agent-2": _definition()}

    assert resolve_agent_node_profile(_node(), {}) is None
    assert resolve_agent_node_profile(_node(), other) is None
    assert resolve_agent_node_profile(_node(), ambiguous) is None
    assert legacy_agent_candidates(_node(), ambiguous) == ("agent-1", "agent-2")
    assert legacy_agent_candidates(_node(), other) == ()


def test_routed_profile_follows_the_route_target_id() -> None:
    catalog = {"agent-1": _definition("generate"), "agent-2": _definition("review")}

    routed = resolve_routed_agent_profile("agent-2", catalog)

    assert routed is not None and routed.legacy_ref is not None
    assert routed.legacy_ref.capability == "review"
    assert resolve_routed_agent_profile("missing", catalog) is None
