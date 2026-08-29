"""Approval gate flow: park → decide (approve / rework / reject) — EXEC-APPROVAL-001.

Covers the executor park write path, job-status derivation, and the
ApprovalDecisionService verdict paths including the rework feedback loop.
"""

from __future__ import annotations

import json

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.approval_decisions import ApprovalDecisionService
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_mapping

APPROVAL_DAG = {
    "key": "approval_demo",
    "label": "Approval Demo",
    "schema_version": 2,
    "nodes": {
        "entry": {"type": "start", "label": "入口"},
        "write": {"label": "写稿", "capability": "write_script", "outputs": ["script.md"]},
        "gate": {
            "type": "approval",
            "label": "逐字稿审批",
            "inputs": ["script.md"],
            "config": {"rework_target": "write"},
        },
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


@pytest.fixture
def approval_setup(job_db: JobQueries, settings):
    definition = workflow_definition_from_mapping(APPROVAL_DAG)
    workspace = job_db.create_workspace(name="approval-ws", default_workflow_key=definition.key)
    workspace_id = str(workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="material",
        source_id="chapter-1",
        run_id="",
        title="第一章",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    # Upstream completed with its artifact present, as the scheduler would leave it.
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "script.md").write_text("# 逐字稿草稿", encoding="utf-8")
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id=%s and node_key='write'",
            (job_id,),
        )
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    service = ApprovalDecisionService(
        job_db,
        settings,
        JobRerunService(job_db, leases, settings),
    )
    return job_db, leases, service, workspace_id, job_id, job_dir


def _node_status(job_db: JobQueries, job_id: str, node_key: str) -> str:
    node = job_db.get_job_node(job_id, node_key)
    assert node is not None
    return str(node["status"])


def _job_status(job_db: JobQueries, job_id: str) -> str:
    job = job_db.get_job(job_id)
    assert job is not None
    return str(job["status"])


def test_park_sets_awaiting_and_job_status(approval_setup):
    job_db, leases, _service, _ws, job_id, _job_dir = approval_setup
    assert leases.park_awaiting_approval(job_id, "gate") is True
    assert _node_status(job_db, job_id, "gate") == "awaiting_approval"
    assert _job_status(job_db, job_id) == "awaiting_approval"
    # Idempotent across duplicate ready candidates: second park is a no-op.
    assert leases.park_awaiting_approval(job_id, "gate") is False


def test_approve_completes_gate_and_writes_decision_artifact(approval_setup):
    job_db, leases, service, workspace_id, job_id, job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    decision = service.decide(
        workspace_id, job_id, "gate", verdict="approved", note="结构OK", decided_by="user:u1"
    )
    assert decision["verdict"] == "approved"
    assert _node_status(job_db, job_id, "gate") == "completed"
    # Downstream is unblocked: the job goes back to queued for publish.
    assert _job_status(job_db, job_id) == "queued"
    payload = json.loads((job_dir / "gate.approval.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "approved"
    assert payload["decided_by"] == "user:u1"
    history = service.list_decisions(workspace_id, job_id)
    assert [d["verdict"] for d in history] == ["approved"]


def test_decide_requires_awaiting_status(approval_setup):
    _job_db, leases, service, workspace_id, job_id, _job_dir = approval_setup
    # Not parked yet → conflict.
    with pytest.raises(ConflictError, match="not awaiting approval"):
        service.decide(workspace_id, job_id, "gate", verdict="approved")
    leases.park_awaiting_approval(job_id, "gate")
    service.decide(workspace_id, job_id, "gate", verdict="approved")
    # Already decided → conflict again (insert-only history stays single).
    with pytest.raises(ConflictError, match="not awaiting approval"):
        service.decide(workspace_id, job_id, "gate", verdict="approved")


def test_decide_rejects_non_approval_nodes(approval_setup):
    _job_db, _leases, service, workspace_id, job_id, _job_dir = approval_setup
    from server.app.services.job_errors import NotFoundError

    with pytest.raises(NotFoundError, match="not an approval node"):
        service.decide(workspace_id, job_id, "write", verdict="approved")


def test_reject_fails_gate_and_job(approval_setup):
    job_db, leases, service, workspace_id, job_id, _job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    service.decide(
        workspace_id, job_id, "gate", verdict="rejected", note="素材质量不足", decided_by="user:u1"
    )
    node = job_db.get_job_node(job_id, "gate")
    assert node["status"] == "failed"
    assert node["failure_category"] == "approval_rejected"
    assert "素材质量不足" in node["error_message"]
    assert _job_status(job_db, job_id) == "failed"


def test_rework_requires_note(approval_setup):
    _job_db, leases, service, workspace_id, job_id, _job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    with pytest.raises(InvalidOperationError, match="reviewer note"):
        service.decide(workspace_id, job_id, "gate", verdict="rework", note="  ")


def test_rework_validates_target_is_upstream(approval_setup):
    _job_db, leases, service, workspace_id, job_id, _job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    with pytest.raises(InvalidOperationError, match="must be an upstream node"):
        service.decide(
            workspace_id, job_id, "gate", verdict="rework", note="重来", rework_target="publish"
        )


def test_rework_resets_upstream_and_writes_feedback(approval_setup):
    job_db, leases, service, workspace_id, job_id, job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    decision = service.decide(
        workspace_id,
        job_id,
        "gate",
        verdict="rework",
        note="第二节和第三节应合并，案例前置",
        decided_by="user:u1",
    )
    assert decision["rework_target"] == "write"
    # The reviewer note became machine input for the regenerating skill.
    feedback = json.loads((job_dir / "review_feedback.json").read_text(encoding="utf-8"))
    assert feedback["note"] == "第二节和第三节应合并，案例前置"
    assert feedback["round"] == 1
    # Upstream reset through the regular rerun machinery; the gate goes
    # stale with the rest of downstream and re-parks after the next run.
    assert _node_status(job_db, job_id, "write") == "pending"
    assert _node_status(job_db, job_id, "gate") == "stale"
    assert _job_status(job_db, job_id) == "queued"
    # Second round: complete the upstream again, park, approve.
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='write'",
            (job_id,),
        )
    assert leases.park_awaiting_approval(job_id, "gate") is True
    service.decide(workspace_id, job_id, "gate", verdict="approved", decided_by="user:u1")
    history = service.list_decisions(workspace_id, job_id)
    assert [d["verdict"] for d in history] == ["approved", "rework"]


def test_rework_leaves_no_decision_when_reset_is_blocked(approval_setup):
    """Codex P1（EXEC-APPROVAL-001）：审计行与节点重置同事务——job 忙时
    打回被拒，决策行不得残留，gate 保持待审可重试。"""
    job_db, leases, service, workspace_id, job_id, _job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    # 模拟并行分支仍在执行：rerun 资格检查应拒绝重置。
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key='publish'",
            (job_id,),
        )
    with pytest.raises(ConflictError):
        service.decide(
            workspace_id, job_id, "gate", verdict="rework", note="重来", decided_by="user:u1"
        )
    assert service.list_decisions(workspace_id, job_id) == []
    assert _node_status(job_db, job_id, "gate") == "awaiting_approval"
