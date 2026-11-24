"""Capability loading boundaries for self-contained agents and legacy nodes (#936)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    serialize_definition,
    workflow_definition_to_response_payload,
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


def test_stored_agent_capability_may_inherit_the_legacy_runtime_default() -> None:
    definition = workflow_definition_from_dict(
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
def test_code_node_capability_defaults_to_empty_optional_label(
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

    loaded = workflow_definition_from_mapping(_raw(node))

    assert loaded.nodes["draft"].capability == ""
    assert loaded.nodes["draft"].key == "draft"


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


def test_failure_policy_round_trips_through_snapshot_and_yaml() -> None:
    definition = workflow_definition_from_mapping(
        _raw(
            {
                "type": "code",
                "failure_default": "business",
                "on_failure": {
                    "business": "rerun_upstream",
                    "technical": "rerun_self",
                    "unknown": "rerun_upstream",
                },
            }
        )
    )

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    echoed = workflow_definition_from_mapping(yaml.safe_load(definition_to_yaml(definition)))

    response_node = next(
        node
        for node in workflow_definition_to_response_payload(definition)["nodes"]
        if node["key"] == "draft"
    )
    assert response_node["failure_default"] == "business"
    assert response_node["on_failure"]["unknown"] == "rerun_upstream"
    for loaded in (definition, restored, echoed):
        assert loaded.nodes["draft"].failure_default == "business"
        assert loaded.nodes["draft"].on_failure == {
            "business": "rerun_upstream",
            "technical": "rerun_self",
            "unknown": "rerun_upstream",
        }


def test_legacy_snapshot_without_failure_policy_keeps_empty_defaults() -> None:
    legacy = _raw({"type": "code"})
    loaded = workflow_definition_from_dict(legacy)

    assert loaded.nodes["draft"].failure_default == ""
    assert loaded.nodes["draft"].on_failure == {}
    response_node = next(
        node
        for node in workflow_definition_to_response_payload(loaded)["nodes"]
        if node["key"] == "draft"
    )
    assert "failure_default" not in response_node
    assert "on_failure" not in response_node


@pytest.mark.parametrize(
    "fields",
    [
        {"failure_default": None},
        {"failure_default": "declared"},
        {"failure_default": 1},
        {"on_failure": None},
        {"on_failure": []},
        {"on_failure": {"other": "rerun_self"}},
        {"on_failure": {"business": "skip"}},
        {"on_failure": {"business": None}},
    ],
)
def test_failure_policy_rejects_invalid_declarations(fields: dict[str, Any]) -> None:
    with pytest.raises(WorkflowDefinitionError, match="on_failure|failure_default"):
        workflow_definition_from_mapping(_raw({"type": "code", **fields}))
