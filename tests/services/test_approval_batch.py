"""Batch approval audit, skip, conflict and concurrency semantics (#1363)."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.services import approval_batch
from server.app.services.approval_decisions import ApprovalDecisionService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_mapping

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "lane": {"type": "string", "default": "standard"},
        "limit": {"type": "integer", "default": 1},
    },
    "required": ["lane"],
}
MANUAL_SCHEMA = {
    "type": "object",
    "properties": {"reviewer_note": {"type": "string"}},
    "required": ["reviewer_note"],
}


def _definition(workspace_id: str, schema: dict[str, Any] | None, *, multi: bool = False):
    gates = {
        "gate": {
            "type": "approval",
            "label": "审批",
            "inputs": ["script.md"],
            "config_schema": schema or {},
        }
    }
    edges = [
        {"from": "entry", "to": "write"},
        {"from": "write", "to": "gate"},
        {"from": "gate", "to": "publish"},
    ]
    if multi:
        gates["gate_b"] = {"type": "approval", "label": "审批 B", "inputs": ["script.md"]}
        edges.extend([{"from": "write", "to": "gate_b"}, {"from": "gate_b", "to": "publish"}])
    raw = {
        "key": workspace_id,
        "label": "Approval batch",
        "schema_version": 2,
        "nodes": {
            "entry": {"type": "start", "label": "入口"},
            "write": {"label": "写稿", "capability": "write", "outputs": ["script.md"]},
            **gates,
            "publish": {"label": "发布", "capability": "publish", "inputs": ["script.md"]},
        },
        "edges": edges,
    }
    return replace(workflow_definition_from_mapping(raw), key=workspace_id)


@pytest.fixture
def batch_setup(job_db: JobQueries, settings):
    workspace = job_db.create_workspace(name="approval-batch-ws")
    workspace_id = str(workspace["id"])
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    service = ApprovalDecisionService(job_db, settings, JobRerunService(job_db, leases, settings))

    def create(
        source_id: str,
        *,
        schema: dict[str, Any] | None = DEFAULT_SCHEMA,
        multi: bool = False,
        park: bool = True,
    ) -> dict[str, Any]:
        definition = _definition(workspace_id, schema, multi=multi)
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
        if park:
            for node_key in ("gate", "gate_b") if multi else ("gate",):
                assert leases.park_awaiting_approval(job_id, node_key)
        return job

    return service, workspace_id, create


def _decide(service: ApprovalDecisionService, workspace_id: str, ids: list[str], **overrides):
    params = {
        "verdict": "approved",
        "note": "",
        "decided_by": "user:batch-tester",
        "batch_id": "batch-test",
        "batch_selection": "ids",
        **overrides,
    }
    return approval_batch.batch_decide(service, workspace_id, ids, **params)


def test_batch_approve_writes_default_payload_and_batch_audit(batch_setup, settings):
    service, workspace_id, create = batch_setup
    job = create("A")
    job_id = str(job["id"])

    results = _decide(service, workspace_id, [job_id])

    assert [result["status"] for result in results] == ["succeeded"]
    decisions = service.list_decisions(workspace_id, job_id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["payload"] == {"lane": "standard", "limit": 1}
    assert decision["batch_id"] == "batch-test"
    assert decision["batch_selection"] == "ids"
    assert decision["decided_by"] == "user:batch-tester"
    artifact = resolve_job_dir(job, settings.jobs_dir) / "gate.approval.json"
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_payload["payload"] == decision["payload"]
    assert artifact_payload["batch_id"] == "batch-test"
    assert artifact_payload["batch_selection"] == "ids"


def test_batch_approve_skips_required_manual_input_without_decision(batch_setup):
    service, workspace_id, create = batch_setup
    job = create("M", schema=MANUAL_SCHEMA)
    job_id = str(job["id"])

    result = _decide(service, workspace_id, [job_id])[0]

    assert result["status"] == "skipped"
    assert result["reason_code"] == "needs_manual_input"
    assert result["node_key"] == "gate"
    assert service.list_decisions(workspace_id, job_id) == []
    assert job_db_node_status(service.job_db, job_id) == "awaiting_approval"


def job_db_node_status(job_db: JobQueries, job_id: str) -> str:
    node = job_db.get_job_node(job_id, "gate")
    assert node is not None
    return str(node["status"])


def test_batch_reject_does_not_require_approval_payload(batch_setup):
    service, workspace_id, create = batch_setup
    job = create("R", schema=MANUAL_SCHEMA)
    job_id = str(job["id"])

    result = _decide(service, workspace_id, [job_id], verdict="rejected", note="终止")[0]

    assert result["status"] == "succeeded"
    decision = service.list_decisions(workspace_id, job_id)[0]
    assert decision["verdict"] == "rejected"
    assert decision["payload"] == {}
    assert decision["batch_id"] == "batch-test"
    assert job_db_node_status(service.job_db, job_id) == "failed"


def test_batch_skips_multiple_or_zero_awaiting_gates(batch_setup):
    service, workspace_id, create = batch_setup
    multi = create("multi", multi=True)
    waiting = create("waiting", park=False)

    results = _decide(service, workspace_id, [str(multi["id"]), str(waiting["id"])])

    assert [result["reason_code"] for result in results] == [
        "multiple_awaiting_gates",
        "not_awaiting_approval",
    ]
    assert service.list_decisions(workspace_id, str(multi["id"])) == []
    assert service.list_decisions(workspace_id, str(waiting["id"])) == []


def test_batch_rechecks_single_gate_inside_guarded_transaction(batch_setup, monkeypatch):
    service, workspace_id, create = batch_setup
    multi = create("multi-race", multi=True)
    job_id = str(multi["id"])
    monkeypatch.setattr(
        service.job_db, "list_awaiting_approval_node_keys", lambda _job_id: ["gate"]
    )

    result = _decide(service, workspace_id, [job_id])[0]

    assert result["status"] == "skipped"
    assert result["reason_code"] == "gate_conflict"
    assert service.list_decisions(workspace_id, job_id) == []
    assert job_db_node_status(service.job_db, job_id) == "awaiting_approval"


def test_batch_dedups_ids_and_reports_missing_job(batch_setup):
    service, workspace_id, create = batch_setup
    job_id = str(create("dedup")["id"])

    results = _decide(service, workspace_id, [f" {job_id} ", job_id, "missing"])

    assert len(results) == 2
    assert results[0]["status"] == "succeeded"
    assert results[1]["reason_code"] == "not_found"
    assert len(service.list_decisions(workspace_id, job_id)) == 1


def test_batch_maps_injected_gate_conflict_to_skipped(batch_setup, monkeypatch):
    service, workspace_id, create = batch_setup
    job = create("stale")
    job_id = str(job["id"])

    def stale(_decision, **_kwargs):
        raise ApprovalGateConflict("Node gate is not awaiting approval (status: pending)")

    monkeypatch.setattr(service.job_db, "approve_gate_atomic", stale)
    result = _decide(service, workspace_id, [job_id])[0]

    assert result["status"] == "skipped"
    assert result["reason_code"] == "gate_conflict"
    assert service.list_decisions(workspace_id, job_id) == []


def test_concurrent_batches_have_one_winner_and_one_audit_row(batch_setup, monkeypatch):
    service, workspace_id, create = batch_setup
    job_id = str(create("race")["id"])
    barrier = threading.Barrier(2)
    original = service.job_db.approve_gate_atomic

    def synchronized(decision, **kwargs):
        barrier.wait(timeout=10)
        return original(decision, **kwargs)

    monkeypatch.setattr(service.job_db, "approve_gate_atomic", synchronized)

    def run(batch_id: str):
        return _decide(service, workspace_id, [job_id], batch_id=batch_id)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("batch-a", "batch-b")))

    assert sorted(result["status"] for result in results) == ["skipped", "succeeded"]
    loser = next(result for result in results if result["status"] == "skipped")
    assert loser["reason_code"] == "gate_conflict"
    decisions = service.list_decisions(workspace_id, job_id)
    assert len(decisions) == 1
    assert decisions[0]["batch_id"] in {"batch-a", "batch-b"}
    assert job_db_node_status(service.job_db, job_id) == "completed"
