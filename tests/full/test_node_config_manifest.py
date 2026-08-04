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


EXECUTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "bank_version": {"type": "string", "default": "v5"},
        "country_id": {"type": "string"},
    },
}


def _executor_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "fetch": WorkflowNode(
                key="fetch",
                label="Fetch",
                capability="fetch",
                config={"bank_version": "v9"},
            )
        },
    )


@pytest.mark.full_gate
def test_executor_node_config_freeze_and_manifest(job_db) -> None:
    """Executor capability schemas join the same freeze chain (spec D15)."""
    from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig

    workspace = job_db.create_workspace("cfg-exec-ws")
    job_db.update_workspace(
        workspace["id"],
        node_config={"wf": {"fetch": {"country_id": "9"}}},
    )
    workspace = job_db.get_workspace(workspace["id"])
    executors = {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=2,
            capabilities={
                "fetch": LocalCapabilityConfig(handler="mod.fetch", config_schema=EXECUTOR_SCHEMA),
            },
        )
    }

    resolved = resolve_workflow_node_configs(_executor_definition(), {}, workspace, executors)
    # defaults → node config → workspace override
    assert resolved == {"fetch": {"bank_version": "v9", "country_id": "9"}}

    mode = SimpleNamespace(key="batch_by_ids", label="IDs", input_field="question_ids", resource="")
    result = enqueue_intake_batch(
        job_db,
        workspace["id"],
        {"workflow_key": "wf", "source_kind": "batch_by_ids"},
        "question",
        ["q1"],
        {},
        mode,
        {"id": "rev-1"},
        resolved,
    )
    batch = job_db.get_batch(str(result["batch"]["id"]))
    payload = json.loads(str(batch["source_payload_json"]))
    frozen = payload["node_config"]["fetch"]
    assert frozen == {"bank_version": "v9", "country_id": "9"}

    # Executor schemas declare non-secret keys only (D16), so the manifest
    # whitelist passes the frozen config through unchanged.
    assert manifest_safe_config(EXECUTOR_SCHEMA, frozen) == frozen
