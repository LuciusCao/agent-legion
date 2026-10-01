"""not_applicable 批量写的代次防护（EXEC-GENERATION-001，#759 审查 P2）。

poll pass 的评估读（scan 的 fat job 行 + 节点行）与批量写
``mark_nodes_not_applicable_many`` 之间若 reset mutation 提交，过期分支
判定会把新代次的 pending 行翻成 not_applicable（守卫
``status in ('pending','ready','stale')`` 照样匹配新行）；job_nodes 不在
scan mark、该写不 bump ``jobs.updated_at`` → 评估缓存不失效 → 节点可能
永卡 not_applicable，极端时 ``sync_job_status`` 把 job 收敛成 completed
而节点从未执行。修复：批量写携带评估时的代次（fat 行的
``execution_generation``），写前在 ``job-mutation:<job_id>`` 锁内复核，
代次不等则放弃本次标记——mark 中的代次已随 bump 变化，下轮自然重评。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import lease_guarded_mutation, mark_nodes_for_rerun
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker


def _branch_definition() -> WorkflowDefinition:
    """a → b 条件边：condition 读 a_out.json 的 $.from，等于 "a" 才走 b。"""
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["a_out.json"],
                outputs=["b_out.json"],
            ),
        },
        edges=[
            WorkflowEdge(
                source="a",
                target="b",
                condition=WorkflowCondition(artifact="a_out.json", path="$.from", equals="a"),
            )
        ],
    )


def test_not_applicable_write_abandoned_when_generation_bumped_mid_pass(tmp_path: Path) -> None:
    """评估读后代次 bump → 本轮 not_applicable 写入被放弃，节点保持 pending，
    下轮（mark 已随代次变化）重评后正常标记。

    交错构造：包装 ``mark_nodes_not_applicable_many``，在委托真实写之前用真
    实 reset 路径（lease_guarded_mutation + mark_nodes_for_rerun：同事务
    bump 代次并重置节点）提交一次 mutation——等价于 reset 落在评估读与批量
    写之间。第一轮写入必须被代次复核放弃（b 保持 reset 后的 pending）；第二
    轮代次相符，标记正常落库（b → not_applicable）。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = _branch_definition()
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    # 条件为假（from != "a"）→ b 被评估为 not_applicable。
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    (job_dir / "a_out.json").write_text('{"from": "x"}', encoding="utf-8")

    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, executor, [definition])
    original_mark = worker.job_db.mark_nodes_not_applicable_many
    bumped = False

    def reset_then_mark(entries: list[tuple[str, list[str], str, int]]) -> None:
        nonlocal bumped
        if entries and not bumped:
            bumped = True
            with lease_guarded_mutation(
                TEST_DATABASE_URL, str(job["id"]), datetime.now(UTC), reject_running_nodes=True
            ) as conn:
                mark_nodes_for_rerun(conn, str(job["id"]), ["b"], {"b": []})
        return original_mark(entries)

    worker.job_db.mark_nodes_not_applicable_many = reset_then_mark  # type: ignore[method-assign]

    worker._poll()

    # 代次不等 → 写入被放弃：b 保持 reset 后的 pending（未被翻成 not_applicable）。
    assert bumped
    node = queries.get_job_node(job["id"], "b")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1  # reset 的新戳原样保留

    # 下轮重评：mark 中的代次已 bump → 缓存 miss → 重新评估；代次相符 → 标记落库。
    worker._poll()

    node = queries.get_job_node(job["id"], "b")
    assert node["status"] == "not_applicable"
    assert node["stale_reason"] == "unselected workflow branch"

    worker.stop()


def test_not_applicable_write_lands_when_generation_matches(tmp_path: Path) -> None:
    """无并发 reset 的正常路径：代次相符 → 批量写照常落库（防过修对照）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = _branch_definition()
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    (job_dir / "a_out.json").write_text('{"from": "x"}', encoding="utf-8")

    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, executor, [definition])

    worker._poll()

    node: dict[str, Any] = queries.get_job_node(job["id"], "b")
    assert node["status"] == "not_applicable"
    assert node["stale_reason"] == "unselected workflow branch"

    worker.stop()
