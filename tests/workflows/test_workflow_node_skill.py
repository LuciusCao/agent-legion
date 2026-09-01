"""Node-level ``skill:`` declaration: loader forms, echo, snapshot (issue #76)."""

from __future__ import annotations

import json
from dataclasses import asdict
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
from server.app.workflows.schema import WorkflowNodeSkill
from server.app.workflows.workflow_node_skill import effective_node_skill

pytestmark = pytest.mark.no_db

_SKILL_KEY = "education-video-problems-generation/review-questions"


def _definition(node_extra: dict[str, Any]):
    return workflow_definition_from_mapping(
        {
            "key": "wf",
            "label": "Wf",
            "nodes": {"do": {"capability": "do_thing", **node_extra}},
        }
    )


def test_skill_string_form_loads_with_latest_ref() -> None:
    """#322: an empty ref normalizes to ``latest`` (follow the repo's HEAD)."""
    node = _definition({"skill": _SKILL_KEY}).nodes["do"]

    assert node.skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="latest")


def test_skill_mapping_form_loads_key_and_ref() -> None:
    node = _definition({"skill": {"key": _SKILL_KEY, "ref": "v1.1.0"}}).nodes["do"]

    assert node.skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="v1.1.0")


def test_skill_mapping_form_ref_defaults_to_latest() -> None:
    node = _definition({"skill": {"key": _SKILL_KEY}}).nodes["do"]

    assert node.skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="latest")


def test_no_skill_defaults_to_none() -> None:
    assert _definition({}).nodes["do"].skill is None


@pytest.mark.parametrize(
    "raw_skill",
    [
        ["group/name"],  # list
        42,  # int
        {"ref": "v1"},  # mapping without key
        {"key": "group/name", "commit": "abc"},  # unknown key
        {"key": "group/name", "ref": 7},  # non-string ref
        "/abs/path",  # absolute path
        "group/../name",  # parent traversal
        "single",  # one segment
        "a/b/c",  # three segments
        "group/",  # empty second segment
    ],
)
def test_skill_invalid_forms_are_rejected(raw_skill: Any) -> None:
    with pytest.raises(WorkflowDefinitionError, match=r"Node do\.skill"):
        _definition({"skill": raw_skill})


def test_skill_echo_roundtrip_string_form() -> None:
    """#322: the loader normalized the ref, so the echo is the mapping form
    carrying ``ref: latest`` (no hidden fallback left in the yaml)."""
    definition = _definition({"skill": _SKILL_KEY})

    echo = yaml.safe_load(definition_to_yaml(definition))
    assert echo["nodes"]["do"]["skill"] == {"key": _SKILL_KEY, "ref": "latest"}
    reloaded = workflow_definition_from_mapping(echo)
    assert reloaded.nodes["do"].skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="latest")


def test_skill_echo_roundtrip_mapping_form() -> None:
    definition = _definition({"skill": {"key": _SKILL_KEY, "ref": "v1.1.0"}})

    echo = yaml.safe_load(definition_to_yaml(definition))
    assert echo["nodes"]["do"]["skill"] == {"key": _SKILL_KEY, "ref": "v1.1.0"}
    reloaded = workflow_definition_from_mapping(echo)
    assert reloaded.nodes["do"].skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="v1.1.0")


def test_skill_survives_revision_snapshot_round_trip() -> None:
    """The asdict revision/intake snapshot keeps the binding (mapping form)."""
    definition = _definition({"skill": {"key": _SKILL_KEY, "ref": "v1.1.0"}})

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))

    assert restored.nodes["do"].skill == WorkflowNodeSkill(key=_SKILL_KEY, ref="v1.1.0")


def test_snapshot_strips_skill_placeholder_on_start_and_approval() -> None:
    """asdict snapshots carry ``skill: None`` on every node; the start/approval
    placeholder must be stripped like capability/execution (loader forbids it)."""
    start_raw: dict[str, Any] = {"type": "start", "skill": None}
    strip_snapshot_placeholders(start_raw)
    assert "skill" not in start_raw

    approval_raw: dict[str, Any] = {"type": "approval", "skill": None}
    strip_snapshot_placeholders(approval_raw)
    assert "skill" not in approval_raw

    # A regular node keeps its binding through the same strip pass.
    node_raw: dict[str, Any] = {
        "type": "node",
        "skill": asdict(WorkflowNodeSkill(key=_SKILL_KEY, ref="v1")),
    }
    strip_snapshot_placeholders(node_raw)
    assert node_raw["skill"] == {"key": _SKILL_KEY, "ref": "v1"}


def test_response_payload_carries_skill() -> None:
    definition = _definition({"skill": {"key": _SKILL_KEY, "ref": "v1.1.0"}})

    nodes = workflow_definition_to_response_payload(definition)["nodes"]
    by_key = {node["key"]: node for node in nodes}
    assert by_key["do"]["skill"] == {"key": _SKILL_KEY, "ref": "v1.1.0"}
    # The injected start node never carries a binding.
    assert by_key["_start"]["skill"] is None

    bare = workflow_definition_to_response_payload(_definition({}))["nodes"]
    assert next(node for node in bare if node["key"] == "do")["skill"] is None


def test_effective_node_skill_prefers_the_node_binding() -> None:
    node = _definition({"skill": {"key": _SKILL_KEY, "ref": "v1.1.0"}}).nodes["do"]

    assert effective_node_skill(node, "other/agent-skill") == (_SKILL_KEY, "v1.1.0")


def test_effective_node_skill_falls_back_to_the_agent_definition() -> None:
    """Legacy ref-less fallback normalizes to ``latest`` (#322)."""
    node = _definition({}).nodes["do"]

    assert effective_node_skill(node, "other/agent-skill") == ("other/agent-skill", "latest")


def test_effective_node_skill_raises_when_neither_side_binds() -> None:
    node = _definition({}).nodes["do"]

    with pytest.raises(ValueError, match="no skill on node or Agent definition"):
        effective_node_skill(node, "")
