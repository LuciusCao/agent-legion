"""Start node ``text_input`` block: load rules, echo/snapshot symmetry, compare."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
import yaml

from server.app.services.workflow_draft_compare import _node_change_fields, _node_field_risks
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
from server.app.workflows.schema import WorkflowTextInput

pytestmark = pytest.mark.no_db

TEMPLATE = "# 歌曲创作需求\n- 参考歌曲：\n- 新歌主题：\n"


def _definition(start_extra: dict[str, Any]):
    return workflow_definition_from_mapping(
        {
            "key": "wf",
            "label": "Wf",
            "nodes": {
                "_start": {"type": "start", **start_extra},
                "intake": {"capability": "intake", "after": ["_start"]},
            },
        }
    )


def _text_input(**overrides: Any) -> dict[str, Any]:
    return {"label": "创作需求", "filename": "创作需求.md", "template": TEMPLATE, **overrides}


def test_text_input_loads_on_start_node() -> None:
    definition = _definition(
        {"accepted_item_types": ["material", "text"], "text_input": _text_input()}
    )

    assert definition.start_node is not None
    assert definition.start_node.text_input == WorkflowTextInput(
        label="创作需求", filename="创作需求.md", template=TEMPLATE
    )


def test_text_input_absent_none_or_all_empty_is_none() -> None:
    assert _definition({}).start_node.text_input is None
    assert _definition({"text_input": None}).start_node.text_input is None
    assert _definition({"text_input": {"label": " ", "filename": ""}}).start_node.text_input is None


def test_text_input_does_not_require_text_in_contract() -> None:
    """Presentation only: the block is inert without ``text`` accepted, not an error."""
    definition = _definition({"accepted_item_types": ["material"], "text_input": _text_input()})
    assert definition.start_node.text_input is not None


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("not-a-mapping", "must be a mapping"),
        ({"placeholder": "x"}, "must be a mapping with keys"),
        ({"label": 42}, "text_input.label"),
        ({"label": "x" * 81}, "text_input.label"),
        ({"template": "x" * (16 * 1024 + 1)}, "text_input.template"),
        ({"filename": "sub/需求.md"}, "bare .md or .txt"),
        ({"filename": "需求.exe"}, "bare .md or .txt"),
        ({"filename": ".hidden.md"}, "bare .md or .txt"),
    ],
)
def test_text_input_shape_errors(raw: Any, match: str) -> None:
    with pytest.raises(WorkflowDefinitionError, match=match):
        _definition({"text_input": raw})


def test_text_input_rejected_on_non_start_nodes() -> None:
    with pytest.raises(WorkflowDefinitionError, match="only valid on a start node"):
        workflow_definition_from_mapping(
            {
                "key": "wf",
                "label": "Wf",
                "nodes": {"intake": {"capability": "intake", "text_input": _text_input()}},
            }
        )


def test_text_input_yaml_echo_round_trip() -> None:
    definition = _definition({"accepted_item_types": ["text"], "text_input": _text_input()})

    echoed = yaml.safe_load(definition_to_yaml(definition))
    assert echoed["nodes"]["_start"]["text_input"] == _text_input()
    reloaded = workflow_definition_from_mapping(echoed)
    assert reloaded.start_node.text_input == definition.start_node.text_input

    # Undeclared → the key is absent from the echo (no ghost change in Studio).
    bare = yaml.safe_load(definition_to_yaml(_definition({})))
    assert "text_input" not in bare["nodes"]["_start"]


def test_text_input_survives_revision_snapshot_round_trip() -> None:
    definition = _definition({"accepted_item_types": ["text"], "text_input": _text_input()})

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))

    assert restored.start_node.text_input == definition.start_node.text_input
    # Definitions without the block snapshot ``text_input: None`` and reload clean.
    plain = workflow_definition_from_dict(json.loads(serialize_definition(_definition({}))))
    assert plain.start_node.text_input is None


def test_snapshot_strip_drops_text_input_off_non_start_nodes() -> None:
    node_raw: dict[str, Any] = {"type": "node", "text_input": None}
    strip_snapshot_placeholders(node_raw)
    assert "text_input" not in node_raw


def test_text_input_in_response_payload() -> None:
    with_block = workflow_definition_to_response_payload(
        _definition({"accepted_item_types": ["text"], "text_input": _text_input()})
    )
    start = next(node for node in with_block["nodes"] if node["key"] == "_start")
    assert start["text_input"] == _text_input()

    without = workflow_definition_to_response_payload(_definition({}))
    start = next(node for node in without["nodes"] if node["key"] == "_start")
    assert start["text_input"] is None


def test_compare_sees_text_input_as_info_change() -> None:
    base = _definition({"accepted_item_types": ["text"]}).start_node
    draft = replace(base, text_input=WorkflowTextInput(template=TEMPLATE))

    assert _node_change_fields(base, draft) == ["text_input"]
    assert _node_field_risks(base, draft) == {"text_input": "info"}
    assert _node_change_fields(base, base) == []
