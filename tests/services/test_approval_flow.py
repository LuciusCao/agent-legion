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
    # Second round: complete the upstream again, park, approve. The rework
    # bumped the job's execution generation (EXEC-GENERATION-001), so the
    # re-park candidate carries the fresh epoch.
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='write'",
            (job_id,),
        )
    generation = int(job_db.get_job(job_id)["execution_generation"])
    assert leases.park_awaiting_approval(job_id, "gate", execution_generation=generation) is True
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


BRANCH_APPROVAL_DAG = {
    "key": "approval_branch_demo",
    "label": "Approval Branch Demo",
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
        # 与 gate 无关的并行分支 B：它的 rerun 只 bump 全局代次，不动 gate 行。
        "side": {"label": "旁支", "capability": "side_task"},
    },
    "edges": [
        {"from": "entry", "to": "write"},
        {"from": "write", "to": "gate"},
        {"from": "gate", "to": "publish"},
        {"from": "entry", "to": "side"},
    ],
}


@pytest.fixture
def branch_approval_setup(job_db: JobQueries, settings):
    definition = workflow_definition_from_mapping(BRANCH_APPROVAL_DAG)
    workspace = job_db.create_workspace(
        name="approval-branch-ws", default_workflow_key=definition.key
    )
    workspace_id = str(workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="material",
        source_id="chapter-2",
        run_id="",
        title="第二章",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "script.md").write_text("# 逐字稿草稿", encoding="utf-8")
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id=%s and node_key in ('write', 'side')",
            (job_id,),
        )
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    service = ApprovalDecisionService(
        job_db,
        settings,
        JobRerunService(job_db, leases, settings),
    )
    return job_db, leases, service, workspace_id, job_id


def test_sibling_branch_rerun_does_not_brick_parked_gate(branch_approval_setup):
    """EXEC-GENERATION-001 审查 P1：分支 B 已完成节点的 rerun 无条件 bump
    全局代次但只给闭包内节点盖戳——分支 A 已 park 的审批门必须仍可决策，
    不得被「行戳 != jobs 现值」误判为过期（awaiting_approval 不可重新 park，
    误判即无恢复路径的 brick）。"""
    job_db, leases, service, workspace_id, job_id = branch_approval_setup
    assert leases.park_awaiting_approval(job_id, "gate") is True
    assert _node_status(job_db, job_id, "gate") == "awaiting_approval"

    result = service.rerun.rerun(workspace_id, job_id, node_key="side")
    assert result["status"] == "succeeded"
    job = job_db.get_job(job_id)
    assert job is not None and int(job["execution_generation"]) == 1
    gate = job_db.get_job_node(job_id, "gate")
    assert gate is not None
    assert gate["status"] == "awaiting_approval"  # 重置闭包不含 gate，行原样保留
    assert int(gate["execution_generation"]) == 0

    decision = service.decide(
        workspace_id, job_id, "gate", verdict="approved", note="OK", decided_by="user:u1"
    )
    assert decision["verdict"] == "approved"
    assert _node_status(job_db, job_id, "gate") == "completed"


def test_decision_after_gate_branch_reset_is_blocked_by_status_guard(branch_approval_setup):
    """gate 所在分支被重置（rerun/upgrade）后，旧决策必须被状态守卫拦住：
    重置把 gate 行带离 awaiting_approval，重新 park 之前任何决策都冲突。"""
    job_db, leases, service, workspace_id, job_id = branch_approval_setup
    leases.park_awaiting_approval(job_id, "gate")
    # 模拟 gate 所在分支的重置提交：行回到 pending 并盖新戳。
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='pending', execution_generation=1"
            " where job_id=%s and node_key='gate'",
            (job_id,),
        )
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            (job_id,),
        )
    with pytest.raises(ConflictError, match="not awaiting approval"):
        service.decide(workspace_id, job_id, "gate", verdict="approved", decided_by="user:u1")
    assert service.list_decisions(workspace_id, job_id) == []
    assert _node_status(job_db, job_id, "gate") == "pending"


def test_rework_rolls_back_staged_outputs_on_unexpected_error(approval_setup, monkeypatch):
    """#759 自审 P1：rework 事务内意外异常（非冲突/非 ValueError 族）时，
    已暂存的产物必须回滚——不允许 DB 未变而文件消失。"""
    job_db, leases, service, workspace_id, job_id, job_dir = approval_setup
    leases.park_awaiting_approval(job_id, "gate")

    def broken_mark(*args, **kwargs):
        raise RuntimeError("db connectivity lost")

    monkeypatch.setattr(job_db, "mark_nodes_for_rerun_in_transaction", broken_mark)

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="db connectivity lost"):
        service.decide(
            workspace_id, job_id, "gate", verdict="rework", note="重写", decided_by="user:u1"
        )

    assert (job_dir / "script.md").read_text(encoding="utf-8") == "# 逐字稿草稿"
    assert _node_status(job_db, job_id, "write") == "completed"
    assert _node_status(job_db, job_id, "gate") == "awaiting_approval"


def test_rework_feedback_survives_when_declared_as_node_output(approval_setup):
    """#759 自审：feedback 名被受影响节点声明为 output 时，评审意见不得
    被同事务的暂存当作旧产物清掉——feedback 在提交后写入。"""
    job_db, leases, service, workspace_id, job_id, job_dir = approval_setup
    # write 节点把默认 feedback 名也声明为 output 的 workflow 变体。
    definition = workflow_definition_from_mapping(
        {
            **APPROVAL_DAG,
            "nodes": {
                **APPROVAL_DAG["nodes"],
                "write": {
                    "label": "写稿",
                    "capability": "write_script",
                    "outputs": ["script.md", "review_feedback.json"],
                },
            },
        }
    )
    ws = job_db.create_workspace(name="approval-ws-fb", default_workflow_key=definition.key)
    WorkflowRevisionService(job_db).ensure_active_revision(str(ws["id"]), definition)
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="material",
        source_id="chapter-2",
        run_id="",
        title="第二章",
        node_keys=list(definition.executable_nodes),
        workspace_id=str(ws["id"]),
    )
    fb_dir = resolve_job_dir(job, job_db.jobs_dir)
    fb_dir.mkdir(parents=True, exist_ok=True)
    (fb_dir / "script.md").write_text("# 旧稿", encoding="utf-8")
    (fb_dir / "review_feedback.json").write_text('{"round": 0}', encoding="utf-8")
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id=%s and node_key='write'",
            (job["id"],),
        )
    leases.park_awaiting_approval(str(job["id"]), "gate")

    service.decide(
        str(ws["id"]),
        str(job["id"]),
        "gate",
        verdict="rework",
        note="案例前置",
        decided_by="user:u1",
    )

    feedback = json.loads((fb_dir / "review_feedback.json").read_text(encoding="utf-8"))
    assert feedback["note"] == "案例前置"
