"""Self-contained agent node profile fields in the workflow loader (#933, D5)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from server.app.workflows.definition import (
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.revision_format import definition_to_yaml, serialize_definition
from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNodeExecution
from server.app.workflows.workflow_node_execution import node_execution_payload
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

pytestmark = pytest.mark.no_db


def _raw(nodes: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {"key": "wf", "label": "WF", "nodes": nodes, **top}


def _agent(**extra: Any) -> dict[str, Any]:
    return {"type": "agent", "capability": "generate", "outputs": ["a.json"], **extra}


def test_agent_node_declares_runtime_and_requires_labels() -> None:
    definition = workflow_definition_from_mapping(
        _raw(
            {
                "gen": _agent(
                    execution={"runtime": "velites", "provider": "p", "model": "m"},
                    requires_labels={"gpu": "yes"},
                )
            }
        )
    )
    node = definition.nodes["gen"]
    assert node.execution.runtime == "velites"
    assert node.requires_labels == {"gpu": "yes"}
    assert is_self_contained_agent_node(node)


def test_unknown_runtime_is_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="execution.runtime must be one of"):
        workflow_definition_from_mapping(_raw({"gen": _agent(execution={"runtime": "openclaw"})}))


@pytest.mark.parametrize(
    "extra",
    [{"execution": {"runtime": "pi"}}, {"requires_labels": {"gpu": "yes"}}],
)
def test_profile_fields_are_agent_only(extra: dict[str, Any]) -> None:
    with pytest.raises(WorkflowDefinitionError, match="only valid on an agent node"):
        workflow_definition_from_mapping(
            _raw({"pkg": {"type": "code", "capability": "package", **extra}})
        )


@pytest.mark.parametrize("labels", [["gpu"], {"gpu": 1}, {"": "x"}])
def test_requires_labels_shape_is_validated(labels: Any) -> None:
    with pytest.raises(WorkflowDefinitionError, match="requires_labels must be a mapping"):
        workflow_definition_from_mapping(
            _raw({"gen": _agent(execution={"runtime": "pi"}, requires_labels=labels)})
        )


def test_workflow_top_level_runtime_defaults_agent_nodes_only() -> None:
    definition = workflow_definition_from_mapping(
        _raw(
            {
                "gen": _agent(),
                "review": _agent(capability="review", execution={"runtime": "pi"}),
                "pkg": {"type": "code", "capability": "package", "after": ["gen"]},
            },
            execution={"runtime": "velites", "provider": "p", "model": "m"},
        )
    )
    assert definition.execution.runtime == "velites"
    assert definition.nodes["gen"].execution.runtime == "velites"
    assert definition.nodes["review"].execution.runtime == "pi"  # node value wins
    assert definition.nodes["pkg"].execution.runtime == ""
    assert not is_self_contained_agent_node(definition.nodes["pkg"])


def test_workflow_top_level_runtime_is_validated() -> None:
    with pytest.raises(WorkflowDefinitionError, match="Workflow execution.runtime"):
        workflow_definition_from_mapping(_raw({"gen": _agent()}, execution={"runtime": "nope"}))


def test_profile_fields_round_trip_through_snapshot_and_yaml_echo() -> None:
    definition = workflow_definition_from_mapping(
        _raw(
            {
                "gen": _agent(requires_labels={"gpu": "yes"}),
                "review": _agent(capability="review", execution={"runtime": "pi"}, after=["gen"]),
                "gate": {"type": "approval", "after": ["review"]},
            },
            execution={"runtime": "velites", "provider": "p", "model": "m"},
        )
    )
    from_snapshot = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    assert from_snapshot.nodes == definition.nodes
    assert from_snapshot.execution == definition.execution
    echoed = yaml.safe_load(definition_to_yaml(definition))
    assert echoed["execution"]["runtime"] == "velites"
    # The baked default is subtracted back out; the node override survives.
    assert "runtime" not in echoed["nodes"]["gen"].get("execution", {})
    assert echoed["nodes"]["review"]["execution"]["runtime"] == "pi"
    assert echoed["nodes"]["gen"]["requires_labels"] == {"gpu": "yes"}
    reloaded = workflow_definition_from_mapping(echoed)
    assert reloaded.nodes == definition.nodes
    assert reloaded.execution == definition.execution


def test_legacy_workflow_echo_and_payload_stay_unchanged() -> None:
    """Undeclared fields never surface: no runtime / requires_labels keys."""
    definition = workflow_definition_from_mapping(_raw({"gen": _agent()}))
    echoed = yaml.safe_load(definition_to_yaml(definition))
    assert "requires_labels" not in echoed["nodes"]["gen"]
    assert "execution" not in echoed
    payload = node_execution_payload(definition.nodes["gen"].execution)
    assert set(payload) == {"provider", "model", "thinking", "prompt", "prompt_mode"}


def test_node_execution_payload_keeps_a_declared_runtime() -> None:
    payload = node_execution_payload(WorkflowNodeExecution(runtime="pi"))
    assert payload["runtime"] == "pi"


def test_response_contract_exposes_the_profile_fields_read_only() -> None:
    """PR #1039 codex R1: the structured workflow payload (revision routes,
    Studio Agent read tools) shows runtime / requires_labels; legacy nodes
    read as empty."""
    from server.app.routes.workflow_contracts import WorkflowDefinitionResponse
    from server.app.workflows.revision_format import workflow_definition_to_response_payload

    definition = workflow_definition_from_mapping(
        _raw(
            {
                "gen": _agent(execution={"runtime": "velites"}, requires_labels={"gpu": "yes"}),
                "legacy": _agent(capability="review", after=["gen"]),
            }
        )
    )
    payload = workflow_definition_to_response_payload(definition)
    raw_legacy = next(node for node in payload["nodes"] if node["key"] == "legacy")
    assert "requires_labels" not in raw_legacy
    assert "runtime" not in raw_legacy["execution"]

    response = WorkflowDefinitionResponse.model_validate(payload)
    nodes = {node.key: node for node in response.nodes}
    assert nodes["gen"].execution.runtime == "velites"
    assert nodes["gen"].requires_labels == {"gpu": "yes"}
    assert nodes["legacy"].execution.runtime == ""
    assert nodes["legacy"].requires_labels == {}
