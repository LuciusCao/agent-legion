"""Capability loading boundaries for self-contained agents and legacy nodes (#936)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    serialize_definition,
)
from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)

pytestmark = pytest.mark.no_db


def _raw(node: dict[str, Any], *, execution: dict[str, str] | None = None) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "key": "ws",
        "label": "Workspace",
        "nodes": {"draft": node},
    }
    if execution is not None:
        raw["execution"] = execution
    return raw


def _agent(capability: str | None = None, *, runtime: str | None = "velites") -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "agent",
        "label": "Draft display",
        "skill": {"key": "capability_optional/draft"},
        "tools": ["read"],
        "config_schema": {"type": "object", "properties": {}},
    }
    if capability is not None:
        node["capability"] = capability
    if runtime is not None:
        node["execution"] = {"runtime": runtime, "provider": "gateway", "model": "test-model"}
    return node


@pytest.mark.parametrize("capability", [None, ""])
def test_self_contained_agent_capability_is_an_empty_optional_label(
    capability: str | None,
) -> None:
    definition = workflow_definition_from_mapping(_raw(_agent(capability)))
    node = definition.nodes["draft"]

    assert node.capability == ""
    assert node.key == "draft"
    assert node.label == "Draft display"
    assert node.execution.runtime == "velites"


def test_agent_capability_may_inherit_the_existing_top_level_runtime_default() -> None:
    definition = workflow_definition_from_mapping(
        _raw(
            _agent(runtime=None),
            execution={"runtime": "pi", "provider": "gateway", "model": "test-model"},
        )
    )

    assert definition.nodes["draft"].capability == ""
    assert definition.nodes["draft"].execution.runtime == "pi"


def test_non_empty_agent_capability_is_preserved() -> None:
    definition = workflow_definition_from_mapping(_raw(_agent("generate")))

    assert definition.nodes["draft"].capability == "generate"


@pytest.mark.parametrize("capability", [None, ""])
def test_legacy_agent_still_requires_its_original_capability(
    capability: str | None,
) -> None:
    with pytest.raises(WorkflowDefinitionError, match="legacy agent capability.*execution.runtime"):
        workflow_definition_from_mapping(_raw(_agent(capability, runtime=None)))


@pytest.mark.parametrize("node_type", ["code", None])
@pytest.mark.parametrize("capability", [None, ""])
def test_code_node_capability_remains_required(
    node_type: str | None, capability: str | None
) -> None:
    node = _agent(capability)
    node.pop("skill")
    node.pop("tools")
    node.pop("execution")
    if node_type is None:
        node.pop("type")
    else:
        node["type"] = node_type

    with pytest.raises(WorkflowDefinitionError, match="capability must be a non-empty string"):
        workflow_definition_from_mapping(_raw(node))


def test_agent_capability_must_still_be_a_string() -> None:
    with pytest.raises(WorkflowDefinitionError, match="capability must be a non-empty string"):
        workflow_definition_from_mapping(_raw(_agent(42)))  # type: ignore[arg-type]


def test_unknown_agent_runtime_still_fails_closed_without_capability() -> None:
    with pytest.raises(WorkflowDefinitionError, match="execution.runtime"):
        workflow_definition_from_mapping(_raw(_agent(runtime="rust")))


def test_empty_capability_round_trips_through_snapshot_and_yaml() -> None:
    definition = workflow_definition_from_mapping(_raw(_agent()))

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    echoed = workflow_definition_from_mapping(yaml.safe_load(definition_to_yaml(definition)))

    assert restored.nodes["draft"].capability == ""
    assert echoed.nodes["draft"].capability == ""
    assert "capability: ''" in definition_to_yaml(definition)
