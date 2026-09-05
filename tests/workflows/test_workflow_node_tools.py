"""Node-level ``tools:`` declaration: loader forms, echo, snapshot (issue #443).

Only ``type: agent`` nodes may declare a tool whitelist; an empty/absent
value means "inherit the Agent definition's tools at dispatch". The loader
deliberately does not validate tool names — runtimes differ, and velites
fails loud on an unknown tool at startup.
"""

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
from server.app.workflows.approval_node import strip_snapshot_placeholders
from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)

pytestmark = pytest.mark.no_db

_TOOLS = ["read", "write"]


def _definition(node_extra: dict[str, Any]):
    return workflow_definition_from_mapping(
        {
            "key": "wf",
            "label": "Wf",
            "nodes": {"do": {"type": "agent", "capability": "do_thing", **node_extra}},
        }
    )


def test_agent_node_tools_load() -> None:
    node = _definition({"tools": _TOOLS}).nodes["do"]

    assert node.tools == tuple(_TOOLS)


def test_agent_node_without_tools_defaults_to_empty() -> None:
    assert _definition({}).nodes["do"].tools == ()


@pytest.mark.parametrize(
    "raw_tools",
    [
        "read",  # bare string
        42,  # int
        ["read", 7],  # non-string item
    ],
)
def test_tools_invalid_forms_are_rejected(raw_tools: Any) -> None:
    with pytest.raises(WorkflowDefinitionError, match=r"do\.tools must be a list of strings"):
        _definition({"tools": raw_tools})


def test_code_node_must_not_declare_tools() -> None:
    with pytest.raises(WorkflowDefinitionError, match=r"do\.tools is only valid on an agent node"):
        workflow_definition_from_mapping(
            {
                "key": "wf",
                "label": "Wf",
                "nodes": {"do": {"capability": "do_thing", "tools": _TOOLS}},
            }
        )


def test_approval_node_must_not_declare_tools() -> None:
    with pytest.raises(WorkflowDefinitionError, match="must not declare tools"):
        workflow_definition_from_mapping(
            {
                "key": "wf",
                "label": "Wf",
                "nodes": {
                    "do": {"capability": "do_thing"},
                    "gate": {"type": "approval", "after": ["do"], "tools": _TOOLS},
                },
            }
        )


def test_tools_echo_roundtrip() -> None:
    definition = _definition({"tools": _TOOLS})

    echo = yaml.safe_load(definition_to_yaml(definition))
    assert echo["nodes"]["do"]["tools"] == _TOOLS
    reloaded = workflow_definition_from_mapping(echo)
    assert reloaded.nodes["do"].tools == tuple(_TOOLS)


def test_tools_echo_omitted_when_undeclared() -> None:
    echo = yaml.safe_load(definition_to_yaml(_definition({})))

    assert "tools" not in echo["nodes"]["do"]


def test_tools_survive_revision_snapshot_round_trip() -> None:
    """The asdict revision/intake snapshot keeps the declaration."""
    definition = _definition({"tools": _TOOLS})

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))

    assert restored.nodes["do"].tools == tuple(_TOOLS)


def test_snapshot_strips_tools_placeholder_on_start_and_approval() -> None:
    """asdict snapshots carry ``tools: []`` on every node; the start/approval
    placeholder must be stripped like capability/execution (loader forbids it)."""
    start_raw: dict[str, Any] = {"type": "start", "tools": []}
    strip_snapshot_placeholders(start_raw)
    assert "tools" not in start_raw

    approval_raw: dict[str, Any] = {"type": "approval", "tools": []}
    strip_snapshot_placeholders(approval_raw)
    assert "tools" not in approval_raw

    # A regular node keeps its declaration through the same strip pass.
    node_raw: dict[str, Any] = {"type": "agent", "tools": list(_TOOLS)}
    strip_snapshot_placeholders(node_raw)
    assert node_raw["tools"] == _TOOLS


def test_legacy_snapshot_without_tools_key_loads() -> None:
    """Snapshots serialized before #443 carry no ``tools`` key at all."""
    payload = {
        "key": "wf",
        "label": "Wf",
        "schema_version": 2,
        "intake": {"modes": {}},
        "nodes": {
            "do": {
                "key": "do",
                "label": "Do",
                "capability": "do_thing",
                "node_type": "agent",
                "after": [],
                "inputs": [],
                "outputs": [],
            }
        },
        "edges": [],
    }

    restored = workflow_definition_from_dict(payload)

    assert restored.nodes["do"].tools == ()


def test_response_payload_carries_tools() -> None:
    definition = _definition({"tools": _TOOLS})

    nodes = workflow_definition_to_response_payload(definition)["nodes"]
    by_key = {node["key"]: node for node in nodes}
    assert by_key["do"]["tools"] == _TOOLS
    # Undeclared (and the injected start node) omit the key entirely.
    assert "tools" not in by_key["_start"]

    bare = workflow_definition_to_response_payload(_definition({}))["nodes"]
    assert "tools" not in next(node for node in bare if node["key"] == "do")
