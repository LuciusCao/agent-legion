"""Full-gate evidence for CONFIG-MANIFEST-001 over a real database."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import manifest_safe_config
from server.app.services.job_intake_enqueue import enqueue_intake_batch
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode

SCHEMA = {
    "type": "object",
    "properties": {
        "page_size": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
        "subject_id": {"type": "string", "default": "math"},
        "api_key": {"type": "string", "secret": True},
    },
}


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "generate": WorkflowNode(
                key="generate",
                label="Generate",
                capability="generate",
                config={"page_size": 20},
            )
        },
    )


@pytest.mark.full_gate
def test_intake_freeze_and_manifest_whitelist(job_db) -> None:
    workspace = job_db.create_workspace("cfg-ws")
    job_db.update_workspace(
        workspace["id"],
        node_config={"wf": {"generate": {"page_size": 5, "api_key": "sekret"}}},
    )
    workspace = job_db.get_workspace(workspace["id"])
    agents = {
        "agent-v1": AgentDefinition(
            capability="generate",
            runtime="pi",
            skill="question/generate",
            config_schema=SCHEMA,
        )
    }

    resolved = resolve_workflow_node_configs(_definition(), agents, workspace)
    # defaults → node config → workspace override
    assert resolved == {"generate": {"page_size": 5, "subject_id": "math", "api_key": "sekret"}}

    mode = SimpleNamespace(key="batch_by_ids", label="IDs", input_field="question_ids", resource="")
    result = enqueue_intake_batch(
        job_db,
        workspace["id"],
        {"workflow_key": "wf", "source_kind": "batch_by_ids"},
        "question",
        ["q1"],
        {},
        {},
        mode,
        {"id": "rev-1"},
        resolved,
    )
    batch = job_db.get_batch(str(result["batch"]["id"]))
    payload = json.loads(str(batch["source_payload_json"]))
    frozen = payload["node_config"]["generate"]
    assert frozen == {"page_size": 5, "subject_id": "math", "api_key": "sekret"}

    manifest_config = manifest_safe_config(SCHEMA, frozen)
    assert "api_key" not in manifest_config
    assert manifest_config == {"page_size": 5, "subject_id": "math"}
