"""Definition-layer rules for ``type: approval`` nodes (EXEC-APPROVAL-001)."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.scheduler import find_ready_nodes, summarize_job_status


def _approval_dag(**gate_overrides):
    gate = {
        "type": "approval",
        "label": "逐字稿审批",
        "inputs": ["script.md"],
        "config": {"rework_target": "write"},
    }
    gate.update(gate_overrides)
    return {
        "key": "approval_demo",
        "label": "Approval Demo",
        "schema_version": 2,
        "nodes": {
            "entry": {"type": "start", "label": "入口"},
            "write": {"label": "写稿", "capability": "write_script", "outputs": ["script.md"]},
            "gate": gate,
            "publish": {
                "label": "发布",
                "capability": "publish_content",
                "inputs": ["script.md"],
                "terminal": {"outcome": "published"},
            },
        },
        "edges": [
            {"from": "entry", "to": "write"},
            {"from": "write", "to": "gate"},
            {"from": "gate", "to": "publish"},
        ],
    }


def test_approval_node_parses_without_capability():
    definition = workflow_definition_from_mapping(_approval_dag())
    gate = definition.nodes["gate"]
    assert gate.node_type == "approval"
    assert gate.capability == ""
    assert gate.config == {"rework_target": "write"}
    # Approval nodes are executable job nodes (unlike start): they enter job_nodes.
    assert "gate" in definition.executable_nodes


@pytest.mark.parametrize(
    "field, value",
    [
        ("capability", "review_script"),
        ("execution", {"provider": "openai"}),
        ("shard", {"count": 2}),
        ("reduce", {"from": "write"}),
        ("config_schema", {"foo": {"type": "string"}}),
    ],
)
def test_approval_node_rejects_execution_fields(field, value):
    with pytest.raises(WorkflowDefinitionError, match="must not declare"):
        workflow_definition_from_mapping(_approval_dag(**{field: value}))


def test_approval_node_rejects_unknown_config_keys():
    with pytest.raises(WorkflowDefinitionError, match="unknown: auto_approve"):
        workflow_definition_from_mapping(
            _approval_dag(config={"rework_target": "write", "auto_approve": True})
        )


def test_approval_node_requires_executable_upstream():
    raw = _approval_dag()
    # Rewire the gate to hang off the start node only: nothing to review.
    raw["edges"] = [
        {"from": "entry", "to": "write"},
        {"from": "entry", "to": "gate"},
        {"from": "gate", "to": "publish"},
    ]
    with pytest.raises(WorkflowDefinitionError, match="incoming edge from an executable node"):
        workflow_definition_from_mapping(raw)


def test_approval_node_snapshot_round_trip():
    import json

    definition = workflow_definition_from_mapping(_approval_dag())
    # Production snapshots go through JSON (workflow_definition_snapshot_json).
    reloaded = workflow_definition_from_dict(json.loads(json.dumps(asdict(definition))))
    gate = reloaded.nodes["gate"]
    assert gate.node_type == "approval"
    assert gate.config == {"rework_target": "write"}
    assert [e.source for e in reloaded.edges] == [e.source for e in definition.edges]


def test_ready_approval_node_is_a_candidate(tmp_path):
    definition = workflow_definition_from_mapping(_approval_dag())
    (tmp_path / "script.md").write_text("draft", encoding="utf-8")
    ready = find_ready_nodes(
        definition,
        {"write": "completed", "gate": "pending", "publish": "pending"},
        tmp_path,
    )
    assert [node.key for node in ready] == ["gate"]


def test_awaiting_approval_blocks_downstream(tmp_path):
    definition = workflow_definition_from_mapping(_approval_dag())
    (tmp_path / "script.md").write_text("draft", encoding="utf-8")
    ready = find_ready_nodes(
        definition,
        {"write": "completed", "gate": "awaiting_approval", "publish": "pending"},
        tmp_path,
    )
    assert ready == []


def test_summarize_job_status_surfaces_awaiting_approval():
    assert summarize_job_status(["completed", "awaiting_approval", "pending"]) == (
        "awaiting_approval"
    )
    # Running parallel branches outrank the gate; failures too.
    assert summarize_job_status(["running", "awaiting_approval"]) == "running"
    assert summarize_job_status(["failed", "awaiting_approval"]) == "failed"
