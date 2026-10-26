"""Batch approval HTTP contracts and async feeder integration (#1363)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_batch_operations import BatchOpsExecutors, JobBatchOpsFeeder
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflows.definition import workflow_definition_from_mapping

WORKSPACE = "approval-batch-api"
DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {"lane": {"type": "string", "default": "standard"}},
    "required": ["lane"],
}
MANUAL_SCHEMA = {
    "type": "object",
    "properties": {"reviewer_note": {"type": "string"}},
    "required": ["reviewer_note"],
}


def _workspace(client: TestClient) -> str:
    response = client.post("/api/workspaces", json={"id": WORKSPACE, "name": "审批批量"})
    assert response.status_code == 200, response.text
    return WORKSPACE


def _create_job(
    client: TestClient, workspace_id: str, source_id: str, schema: dict[str, Any] | None = None
) -> str:
    job_db = client.app.state.job_db
    raw = {
        "key": workspace_id,
        "label": "Approval batch API",
        "schema_version": 2,
        "nodes": {
            "entry": {"type": "start", "label": "入口"},
            "write": {"label": "写稿", "capability": "write", "outputs": ["script.md"]},
            "gate": {
                "type": "approval",
                "label": "审批",
                "inputs": ["script.md"],
                "config_schema": schema or DEFAULT_SCHEMA,
            },
            "publish": {"label": "发布", "capability": "publish", "inputs": ["script.md"]},
        },
        "edges": [
            {"from": "entry", "to": "write"},
            {"from": "write", "to": "gate"},
            {"from": "gate", "to": "publish"},
        ],
    }
    definition = replace(workflow_definition_from_mapping(raw), key=workspace_id)
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type="material",
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id=%s and node_key='write'",
            (job_id,),
        )
    leases = ExecutorLeaseRepository(job_db, data_dir=client.app.state.settings.data_dir)
    assert leases.park_awaiting_approval(job_id, "gate")
    return job_id


def _drain(client: TestClient) -> None:
    job_db = client.app.state.job_db
    settings = client.app.state.settings
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    feeder = JobBatchOpsFeeder(
        job_db,
        settings,
        BatchOpsExecutors(
            rerun=services.rerun,
            deletion=services.deletion,
            execution=services.execution,
            pause=services.pause,
            upgrade=services.workflow_upgrade,
            packages=JobPackageService(job_db, settings),
            approvals=services.approvals,
        ),
    )
    while feeder.consume_once():
        pass


def test_single_payload_unblocks_manual_gate_that_batch_skips(client: TestClient) -> None:
    ws = _workspace(client)
    job_id = _create_job(client, ws, "manual", MANUAL_SCHEMA)
    batch_url = f"/api/workspaces/{ws}/jobs/batch-approve"

    skipped = client.post(batch_url, json={"job_ids": [job_id]})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["results"][0]["reason_code"] == "needs_manual_input"

    single_url = f"/api/workspaces/{ws}/jobs/{job_id}/nodes/gate/approval"
    missing = client.post(single_url, json={"verdict": "approved"})
    assert missing.status_code == 400, missing.text
    approved = client.post(
        single_url,
        json={"verdict": "approved", "payload": {"reviewer_note": "已人工核对"}},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["payload"] == {"reviewer_note": "已人工核对"}
    assert approved.json()["batch_id"] is None

    history = client.get(f"/api/workspaces/{ws}/jobs/{job_id}/approvals").json()["decisions"]
    assert history[0]["payload"] == {"reviewer_note": "已人工核对"}
    assert history[0]["batch_selection"] is None


def test_filter_approve_and_explicit_reject_keep_batch_context(client: TestClient) -> None:
    ws = _workspace(client)
    first = _create_job(client, ws, "first")
    second = _create_job(client, ws, "second")
    excluded = _create_job(client, ws, "excluded")

    response = client.post(
        f"/api/workspaces/{ws}/jobs/batch-approve",
        json={"filter": {"status": "awaiting_approval"}, "exclude_ids": [excluded]},
    )
    assert response.status_code == 200, response.text
    assert {item["job_id"] for item in response.json()["results"]} == {first, second}
    assert {item["status"] for item in response.json()["results"]} == {"succeeded"}
    for job_id in (first, second):
        decision = client.get(f"/api/workspaces/{ws}/jobs/{job_id}/approvals").json()["decisions"][
            0
        ]
        assert decision["batch_selection"] == "filter"
        assert decision["batch_id"]
        assert decision["decided_by"].startswith("user:")
        assert decision["payload"] == {"lane": "standard"}
    waiting = client.app.state.job_db.get_job_node(excluded, "gate")
    assert waiting["status"] == "awaiting_approval"

    rejected = client.post(
        f"/api/workspaces/{ws}/jobs/batch-reject",
        json={"job_ids": [excluded], "note": "批量终止"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["results"][0]["operation"] == "reject"
    decision = client.get(f"/api/workspaces/{ws}/jobs/{excluded}/approvals").json()["decisions"][0]
    assert decision["verdict"] == "rejected"
    assert decision["batch_selection"] == "ids"
    assert client.get(f"/api/jobs/{excluded}").json()["job"]["status"] == "failed"


def test_async_filter_uses_operation_id_as_batch_id(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 0
    )
    ws = _workspace(client)
    job_id = _create_job(client, ws, "async")

    accepted = client.post(
        f"/api/workspaces/{ws}/jobs/batch-approve",
        json={"filter": {"status": "awaiting_approval"}},
    )
    assert accepted.status_code == 202, accepted.text
    operation = accepted.json()["operation"]
    assert operation["kind"] == "approve"
    op_id = operation["id"]
    assert client.get(f"/api/workspaces/{ws}/jobs/{job_id}/approvals").json()["decisions"] == []

    _drain(client)

    status = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}").json()
    assert status["status"] == "completed"
    assert status["succeeded_count"] == 1
    results = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results").json()
    assert results["results"][0]["result"]["operation"] == "approve"
    decision = client.get(f"/api/workspaces/{ws}/jobs/{job_id}/approvals").json()["decisions"][0]
    assert decision["batch_id"] == op_id
    assert decision["batch_selection"] == "filter"


def test_async_approval_pause_resume_does_not_duplicate_decisions(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 0
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    ws = _workspace(client)
    job_ids = [_create_job(client, ws, "pause-a"), _create_job(client, ws, "pause-b")]
    accepted = client.post(f"/api/workspaces/{ws}/jobs/batch-approve", json={"job_ids": job_ids})
    assert accepted.status_code == 202, accepted.text
    op_id = accepted.json()["operation"]["id"]
    job_db = client.app.state.job_db
    settings = client.app.state.settings
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    feeder = JobBatchOpsFeeder(
        job_db,
        settings,
        BatchOpsExecutors(
            rerun=services.rerun,
            deletion=services.deletion,
            execution=services.execution,
            pause=services.pause,
            upgrade=services.workflow_upgrade,
            packages=JobPackageService(job_db, settings),
            approvals=services.approvals,
        ),
    )
    original_advance = job_db.advance_job_batch_operation

    def pause_before_advance(op_id: str, **kwargs: Any) -> Any:
        job_db.transition_job_batch_operation(op_id, from_statuses=("running",), to_status="paused")
        return original_advance(op_id, **kwargs)

    monkeypatch.setattr(job_db, "advance_job_batch_operation", pause_before_advance)
    assert feeder.consume_once() is True
    decisions_url = f"/api/workspaces/{ws}/jobs/{{job_id}}/approvals"
    assert len(client.get(decisions_url.format(job_id=job_ids[0])).json()["decisions"]) == 1
    assert client.get(decisions_url.format(job_id=job_ids[1])).json()["decisions"] == []
    assert feeder.consume_once() is False

    monkeypatch.setattr(job_db, "advance_job_batch_operation", original_advance)
    resumed = client.post(f"/api/workspaces/{ws}/job-batch-operations/{op_id}/resume")
    assert resumed.status_code == 200, resumed.text
    _drain(client)

    for job_id in job_ids:
        decisions = client.get(decisions_url.format(job_id=job_id)).json()["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["batch_id"] == op_id
    operation = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}").json()
    assert operation["status"] == "completed"
    assert operation["processed_count"] == operation["succeeded_count"] == 2


def test_batch_approval_selection_and_auth_validation(
    client: TestClient, anon_client: TestClient
) -> None:
    ws = _workspace(client)
    job_id = _create_job(client, ws, "validation")
    ambiguous = client.post(
        f"/api/workspaces/{ws}/jobs/batch-approve",
        json={"job_ids": [job_id], "filter": {"status": "awaiting_approval"}},
    )
    assert ambiguous.status_code == 422
    assert (
        anon_client.post(
            f"/api/workspaces/{ws}/jobs/batch-reject", json={"job_ids": [job_id]}
        ).status_code
        == 401
    )
