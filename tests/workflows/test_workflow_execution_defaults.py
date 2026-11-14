"""Authoring and stored legacy execution-default materialization (#805)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from server.app.workflows.loader import (
    load_workflow_definition,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.revision_format import definition_to_yaml, serialize_definition
from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNodeExecution

pytestmark = pytest.mark.no_db

_STORED = {
    "key": "wf",
    "label": "WF",
    "execution": {
        "provider": "top-provider",
        "model": "top-model",
        "thinking": "low",
        "runtime": "velites",
    },
    "nodes": {
        "agent_node": {
            "node_type": "agent",
            "capability": "review",
            "execution": {"provider": "node-provider", "runtime": "pi", "prompt": "extra"},
        },
        "empty_agent": {"node_type": "agent", "capability": "generate"},
        "legacy_alias": {"node_type": "node", "capability": "legacy"},
        "raw_code": {"node_type": "code", "capability": "package"},
        "raw_code_with_dead_values": {
            "node_type": "code",
            "capability": "package",
            "execution": {"provider": "dead-code-provider"},
        },
    },
}


def _stored(raw: dict | None = None):
    return workflow_definition_from_dict(copy.deepcopy(raw if raw is not None else _STORED))


def _authoring(raw: dict | None = None) -> dict:
    payload = copy.deepcopy(raw if raw is not None else _STORED)
    for node in payload["nodes"].values():
        if "node_type" in node:
            node["type"] = node.pop("node_type")
    return payload


def _mapping(raw: dict | None = None):
    return workflow_definition_from_mapping(_authoring(raw))


@pytest.mark.parametrize("execution", [{"provider": "p"}, {}, None, "invalid"])
def test_mapping_authoring_rejects_any_top_level_execution(execution) -> None:
    raw = _authoring()
    raw["execution"] = execution

    with pytest.raises(WorkflowDefinitionError) as caught:
        _mapping(raw)

    message = str(caught.value)
    assert "#805" in message
    assert "nodes.agent_node.execution" in message
    assert "nodes.empty_agent.execution" in message
    assert "do not re-add" in message


def test_yaml_file_authoring_uses_the_same_strict_rejection(tmp_path: Path) -> None:
    raw = _authoring()
    raw["execution"] = None
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(WorkflowDefinitionError, match="#805"):
        load_workflow_definition(path)


def test_stored_legacy_materializes_agent_nodes_and_preserves_mixed_overrides() -> None:
    definition = _stored()

    agent = definition.nodes["agent_node"]
    assert agent.execution.provider == "node-provider"
    assert agent.execution.model == "top-model"
    assert agent.execution.thinking == "low"
    assert agent.execution.runtime == "pi"
    assert agent.execution.prompt == "extra"

    empty = definition.nodes["empty_agent"].execution
    assert empty.provider == "top-provider"
    assert empty.model == "top-model"
    assert empty.thinking == "low"
    assert empty.runtime == "velites"


def test_stored_legacy_alias_keeps_historical_non_runtime_defaults() -> None:
    execution = _stored().nodes["legacy_alias"].execution
    assert execution.provider == "top-provider"
    assert execution.model == "top-model"
    assert execution.thinking == "low"
    assert execution.runtime == ""


def test_stored_legacy_does_not_touch_non_agent_raw_nodes() -> None:
    definition = _stored()
    assert definition.nodes["raw_code"].execution == WorkflowNodeExecution()
    assert definition.nodes["raw_code_with_dead_values"].execution.provider == "dead-code-provider"
    assert definition.start_node is not None
    assert definition.start_node.execution == WorkflowNodeExecution()


def test_stored_legacy_without_runtime_does_not_invent_one() -> None:
    raw = copy.deepcopy(_STORED)
    del raw["execution"]["runtime"]
    definition = _stored(raw)
    assert definition.nodes["empty_agent"].execution.runtime == ""
    assert definition.nodes["empty_agent"].execution.provider == "top-provider"


def test_new_definition_and_serialization_have_no_top_level_execution() -> None:
    definition = _stored()
    assert not hasattr(definition, "execution")
    assert "execution" not in json.loads(serialize_definition(definition))


@pytest.mark.parametrize(
    ("execution", "message"),
    [
        ("nope", "Workflow execution must be a mapping"),
        ({"provider": 42}, "execution.provider must be a string"),
        ({"runtime": "unknown"}, "Workflow execution.runtime"),
        ({"prompt": "global"}, "execution.prompt is not allowed"),
        ({"prompt_mode": "append"}, "execution.prompt_mode is not allowed"),
    ],
)
def test_stored_legacy_shape_is_still_validated(execution, message: str) -> None:
    raw = copy.deepcopy(_STORED)
    raw["execution"] = execution
    with pytest.raises(WorkflowDefinitionError, match=message):
        _stored(raw)


def test_stored_snapshot_round_trip_preserves_effective_node_profiles() -> None:
    definition = _stored()
    reloaded = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    assert reloaded.nodes == definition.nodes
    assert "execution" not in json.loads(serialize_definition(reloaded))


def test_echo_has_no_top_level_block_and_mapping_round_trip_is_lossless() -> None:
    definition = _stored()
    echoed = yaml.safe_load(definition_to_yaml(definition))

    assert "execution" not in echoed
    assert echoed["nodes"]["agent_node"]["execution"] == {
        "provider": "node-provider",
        "model": "top-model",
        "thinking": "low",
        "prompt": "extra",
        "runtime": "pi",
    }
    assert echoed["nodes"]["empty_agent"]["execution"] == {
        "provider": "top-provider",
        "model": "top-model",
        "thinking": "low",
        "runtime": "velites",
    }
    assert "execution" not in echoed["nodes"]["raw_code"]
    assert _mapping(echoed).nodes == definition.nodes


def test_poisoned_approval_snapshot_still_heals_on_stored_read() -> None:
    raw = {
        "key": "wf",
        "label": "WF",
        "schema_version": 2,
        "execution": {"provider": "top-provider", "model": "top-model"},
        "nodes": {
            "entry": {"node_type": "start", "label": "入口"},
            "write": {
                "node_type": "agent",
                "label": "写稿",
                "capability": "write_script",
                "outputs": ["script.md"],
            },
            "gate": {
                "node_type": "approval",
                "label": "审批",
                "inputs": ["script.md"],
                "execution": {"provider": "dead-value"},
            },
        },
        "edges": [
            {"source": "entry", "target": "write"},
            {"source": "write", "target": "gate"},
        ],
    }
    definition = _stored(raw)
    assert definition.nodes["gate"].execution == WorkflowNodeExecution()
    assert definition.nodes["write"].execution.provider == "top-provider"


def test_authoring_still_rejects_execution_on_approval() -> None:
    raw = {
        "key": "wf",
        "label": "WF",
        "nodes": {
            "gate": {
                "type": "approval",
                "label": "审批",
                "execution": {"provider": "node-provider"},
            }
        },
    }
    with pytest.raises(WorkflowDefinitionError, match="must not declare execution"):
        workflow_definition_from_mapping(raw)
