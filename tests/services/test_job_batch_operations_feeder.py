"""JobBatchOpsFeeder tests (#946): the batch-ops drip-feed loop's semantics.

Drives ``consume_once`` synchronously (the asyncio wrapper is
job_batch_ops_background, whose life-support contract mirrors
test_run_service_batch_feeder.py's). Covers every kind's per-job semantics
against the sync path's expectations, the replay guard (recorded seqs are
never re-executed), chunk-level containment, the watermark gate, stale claim
reclaim, and batch-level failure containment.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.services.job_batch_operations import (
    BatchOpsExecutors,
    JobBatchOpsFeeder,
    submit_operation,
    submit_operation_if_large,
)
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from tests.helpers import load_demo_legacy_intake_definition, publish_builtin_revision

WORKSPACE = "ops-feeder-ws"
NODE_KEYS = ["intake_knowledge_points", "write_script", "review_script", "publish_content"]


def _executors(job_db: JobQueries, settings: Settings) -> BatchOpsExecutors:
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    return BatchOpsExecutors(
        rerun=services.rerun,
        deletion=services.deletion,
        execution=services.execution,
        pause=services.pause,
        upgrade=services.workflow_upgrade,
        packages=JobPackageService(job_db, settings),
    )


def _feeder(job_db: JobQueries, settings: Settings) -> JobBatchOpsFeeder:
    return JobBatchOpsFeeder(job_db, settings, _executors(job_db, settings))


def _drain(feeder: JobBatchOpsFeeder) -> None:
    while feeder.consume_once():
        pass


@pytest.fixture
def workspace(job_db: JobQueries) -> str:
    created = job_db.create_workspace(WORKSPACE)
    publish_builtin_revision(job_db, created["id"])
    return str(created["id"])


def _create_job(
    job_db: JobQueries, workspace_id: str, source_id: str, *, status: str | None = None
) -> dict[str, Any]:
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=list(NODE_KEYS),
        workspace_id=workspace_id,
    )
    if status is not None:
        job_db.update_job_status(job["id"], status)
    return job


def _submit(job_db: JobQueries, kind: str, ids: list[str], params: dict | None = None) -> str:
    row = submit_operation(job_db, WORKSPACE, kind, ids, params or {}, "user:tester")
    return str(row["id"])


def _results_by_job(job_db: JobQueries, op_id: str) -> dict[str, dict[str, Any]]:
    rows = job_db.list_job_batch_operation_results_page(op_id, limit=10000)
    return {str(row["job_id"]): json.loads(str(row["payload_json"])) for row in rows}


def test_threshold_dispatch(workspace, job_db, settings, monkeypatch) -> None:
    """≤ 阈值保持同步快路径（None），> 阈值入队；阈值 env 常量可 monkeypatch。"""
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 2
    )
    assert (
        submit_operation_if_large(job_db, WORKSPACE, "pause", ["a", "b"], {}, "user:tester") is None
    )
    row = submit_operation_if_large(job_db, WORKSPACE, "pause", ["a", "b", "c"], {}, "user:tester")
    assert row is not None
    assert row["status"] == "pending" and row["items_total"] == 3
    # dedup kinds 受理时归一化（strip + 去重），与同步路径的消费方式一致。
    row = submit_operation(job_db, WORKSPACE, "delete", [" x ", "x", "", "y"], {}, "user:tester")
    spec = json.loads(row["target_spec_json"])
    assert spec["job_ids"] == ["x", "y"]


def test_pause_resume_round_trip(workspace, job_db, settings) -> None:
    jobs = [_create_job(job_db, WORKSPACE, f"P{i}") for i in range(3)]
    ids = [str(job["id"]) for job in jobs]

    op_id = _submit(job_db, "pause", ids, {"reason": "hold", "operator": "user:tester"})
    _drain(_feeder(job_db, settings))

    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "completed"
    assert (op["succeeded_count"], op["skipped_count"], op["failed_count"]) == (3, 0, 0)
    assert op["next_index"] == 3 and op["finished_at"] is not None
    results = _results_by_job(job_db, op_id)
    assert {r["status"] for r in results.values()} == {"succeeded"}
    assert {r["operation"] for r in results.values()} == {"pause"}
    for job_id in ids:
        assert job_db.get_job(job_id)["execution_paused"]
    assert job_db.summarize_job_batch_operation_reasons(op_id) == {}

    # resume 接力：同样的入队形态。
    op_id = _submit(job_db, "resume", ids)
    _drain(_feeder(job_db, settings))
    assert job_db.get_job_batch_operation(op_id)["status"] == "completed"
    for job_id in ids:
        assert not job_db.get_job(job_id)["execution_paused"]


def test_pause_per_job_semantics_match_sync(workspace, job_db, settings) -> None:
    """逐 job 语义：terminal / already-paused / missing 的 skipped/failed 与
    同步 batch_pause 的判定逐字一致。"""
    paused_job = _create_job(job_db, WORKSPACE, "P-paused")
    job_db.mark_execution_paused(paused_job["id"], "earlier")
    terminal_job = _create_job(job_db, WORKSPACE, "P-done", status="failed")
    ok_job = _create_job(job_db, WORKSPACE, "P-ok")

    op_id = _submit(
        job_db,
        "pause",
        [str(paused_job["id"]), str(terminal_job["id"]), str(ok_job["id"]), "missing-job"],
    )
    _drain(_feeder(job_db, settings))

    results = _results_by_job(job_db, op_id)
    assert results[str(paused_job["id"])]["reason_code"] == "already_paused"
    assert results[str(terminal_job["id"])]["reason_code"] == "terminal"
    assert results[str(ok_job["id"])]["status"] == "succeeded"
    assert results["missing-job"]["reason_code"] == "not_found"
    op = job_db.get_job_batch_operation(op_id)
    assert (op["succeeded_count"], op["skipped_count"], op["failed_count"]) == (1, 2, 1)
    assert job_db.summarize_job_batch_operation_reasons(op_id) == {
        "already_paused": 1,
        "terminal": 1,
        "not_found": 1,
    }


def test_rerun_async_matches_batch_semantics(workspace, job_db, settings) -> None:
    ok = _create_job(job_db, WORKSPACE, "R-ok", status="failed")
    running = _create_job(job_db, WORKSPACE, "R-running", status="failed")
    job_db.update_job_node(running["id"], "write_script", status="running")

    op_id = _submit(
        job_db,
        "rerun",
        [str(ok["id"]), str(running["id"]), "missing-job"],
        {"node_key": "write_script", "from_failed_node": False},
    )
    _drain(_feeder(job_db, settings))

    results = _results_by_job(job_db, op_id)
    assert results[str(ok["id"])]["status"] == "succeeded"
    assert results[str(ok["id"])]["node_key"] == "write_script"
    assert results[str(running["id"])]["status"] == "skipped"
    assert results["missing-job"]["reason_code"] == "not_found"


def test_rerun_by_failure_async(workspace, job_db, settings) -> None:
    def _fail(job: dict[str, Any], category: str) -> None:
        run = job_db.start_node_run(job["id"], "write_script", ["cmd"], f"logs/{job['id']}.log")
        with job_db.connect() as conn:
            conn.execute(
                "update node_runs set status='failed', error_message='boom',"
                " failure_category=%s, failure_detail='d', finished_at=current_timestamp"
                " where id=%s",
                (category, run["id"]),
            )
            conn.execute(
                "update job_nodes set status='failed' where job_id=%s and node_key='write_script'",
                (job["id"],),
            )
            conn.execute("update jobs set status='failed' where id=%s", (job["id"],))

    technical = _create_job(job_db, WORKSPACE, "F-tech")
    business = _create_job(job_db, WORKSPACE, "F-biz")
    _fail(technical, "technical")
    _fail(business, "business")

    op_id = _submit(
        job_db,
        "rerun_by_failure",
        [str(technical["id"]), str(business["id"])],
        {"category": "technical", "strategy": "auto", "from_node_key": None},
    )
    _drain(_feeder(job_db, settings))

    results = _results_by_job(job_db, op_id)
    assert results[str(technical["id"])]["status"] == "succeeded"
    assert results[str(technical["id"])]["rerun_nodes"] == ["write_script"]
    assert results[str(business["id"])]["status"] == "skipped"
    assert results[str(business["id"])]["reason_code"] == "no_matching_failure"


def test_delete_async(workspace, job_db, settings) -> None:
    victim = _create_job(job_db, WORKSPACE, "D-1")
    survivor = _create_job(job_db, WORKSPACE, "D-2")

    op_id = _submit(job_db, "delete", [str(victim["id"])])
    _drain(_feeder(job_db, settings))

    assert job_db.get_job_batch_operation(op_id)["status"] == "completed"
    assert _results_by_job(job_db, op_id)[str(victim["id"])]["status"] == "succeeded"
    assert job_db.get_job(str(victim["id"])) is None
    assert job_db.get_job(str(survivor["id"])) is not None


def test_run_to_async(workspace, job_db, settings) -> None:
    job = _create_job(job_db, WORKSPACE, "T-1")

    op_id = _submit(job_db, "run_to", [str(job["id"])], {"target_node_key": "publish_content"})
    _drain(_feeder(job_db, settings))

    result = _results_by_job(job_db, op_id)[str(job["id"])]
    assert result["status"] == "succeeded"
    assert result["node_key"] == "publish_content"


def test_upgrade_workflow_async(workspace, job_db, settings) -> None:
    job = _create_job(job_db, WORKSPACE, "U-1")
    # A second publish makes the job's revision stale (route-test precedent).
    WorkflowRevisionService(job_db).publish_workspace_revision(
        WORKSPACE, load_demo_legacy_intake_definition()
    )

    op_id = _submit(job_db, "upgrade_workflow", [str(job["id"])], {"mode": "clean"})
    _drain(_feeder(job_db, settings))

    result = _results_by_job(job_db, op_id)[str(job["id"])]
    assert result["status"] == "succeeded"
    assert result["operation"] == "upgrade_workflow"
    assert result["mode"] == "clean"


def test_package_async_marks_eligibility_and_packages_once(workspace, job_db, settings) -> None:
    def _complete(source_id: str) -> dict[str, Any]:
        job = _create_job(job_db, WORKSPACE, source_id, status="completed")
        job_dir = resolve_job_dir(job, settings.jobs_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "question_context.json").write_text(
            f'{{"question_id":"{source_id}"}}', encoding="utf-8"
        )
        return job

    done_a = _complete("PK-a")
    done_b = _complete("PK-b")
    queued = _create_job(job_db, WORKSPACE, "PK-queued")

    op_id = _submit(job_db, "package", [str(done_a["id"]), str(queued["id"]), str(done_b["id"])])
    _drain(_feeder(job_db, settings))

    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "completed"
    assert (op["succeeded_count"], op["failed_count"]) == (2, 1)
    results = _results_by_job(job_db, op_id)
    assert results[str(queued["id"])]["reason_code"] == "not_completed"
    output = json.loads(op["output_json"])
    assert output["package_filename"].endswith(".zip")
    assert (settings.packages_dir / f"workspace-{WORKSPACE}" / output["package_filename"]).exists()
    # 打包只发生一次（完成钩）且源 job 已标记 packed。
    packages = job_db.list_workspace_packages(WORKSPACE, limit=10)
    assert len(packages) == 1
    with job_db.connect() as conn:
        packed = conn.execute(
            "select packed from jobs where id = any(%s) order by id",
            ([str(done_a["id"]), str(done_b["id"])],),
        ).fetchall()
    assert [row["packed"] for row in packed] == [1, 1]


def test_clear_packed_async(workspace, job_db, settings) -> None:
    job = _create_job(job_db, WORKSPACE, "C-1", status="completed")
    job_db.set_jobs_packed([str(job["id"])], packed=1)

    op_id = _submit(job_db, "clear_packed", [str(job["id"]), "missing-job"])
    _drain(_feeder(job_db, settings))

    results = _results_by_job(job_db, op_id)
    assert results[str(job["id"])]["status"] == "succeeded"
    assert results["missing-job"]["reason_code"] == "not_found"
    with job_db.connect() as conn:
        row = conn.execute("select packed from jobs where id=%s", (str(job["id"]),)).fetchone()
    assert row["packed"] == 0


def test_replay_guard_never_reexecutes_recorded_seqs(
    workspace, job_db, settings, monkeypatch
) -> None:
    """崩溃重放安全：游标回拨到已记录 seq 时 feeder 只补簿记，不重复执行
    （rerun/package 天然不幂等——done-seqs 探测是重放安全机制）。"""
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    jobs = [_create_job(job_db, WORKSPACE, f"G{i}") for i in range(2)]
    ids = [str(job["id"]) for job in jobs]
    op_id = _submit(job_db, "pause", ids)

    executors = _executors(job_db, settings)
    pause_calls: list[str] = []
    original_pause = executors.pause.pause

    def counting_pause(workspace_id: str, job_id: str, *args: Any, **kwargs: Any) -> Any:
        pause_calls.append(job_id)
        return original_pause(workspace_id, job_id, *args, **kwargs)

    monkeypatch.setattr(executors.pause, "pause", counting_pause)
    feeder = JobBatchOpsFeeder(job_db, settings, executors)

    assert feeder.consume_once() is True  # chunk [0,1) 提交并推进
    assert pause_calls == [ids[0]]
    # 模拟崩溃：游标回拨到 0 且 claim 停滞（结果行仍在——提交先于游标）。
    with job_db.connect() as conn:
        conn.execute(
            "update job_batch_operations set next_index=0, status='running',"
            " updated_at=current_timestamp - interval '1 hour' where id=%s",
            (op_id,),
        )

    _drain(feeder)

    assert pause_calls == [ids[0], ids[1]]  # seq0 未重执行
    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "completed"
    assert op["succeeded_count"] == 2  # 重放没有双记
    assert len(job_db.list_job_batch_operation_results_page(op_id, limit=10)) == 2


def test_chunk_failure_becomes_per_job_failed_results(
    workspace, job_db, settings, monkeypatch
) -> None:
    """chunk 级意外（非 JobOperationError）→ 逐 job failed/internal_error +
    chunk_errors 记录，整批照常 completed（#759 P1 归一化先例）。"""
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    bad = _create_job(job_db, WORKSPACE, "X-bad")
    good = _create_job(job_db, WORKSPACE, "X-good")

    executors = _executors(job_db, settings)
    original_delete = executors.deletion.delete

    def flaky_delete(workspace_id: str, job_id: str) -> Any:
        if job_id == str(bad["id"]):
            raise RuntimeError("simulated db hiccup")
        return original_delete(workspace_id, job_id)

    monkeypatch.setattr(executors.deletion, "delete", flaky_delete)
    feeder = JobBatchOpsFeeder(job_db, settings, executors)

    op_id = _submit(job_db, "delete", [str(bad["id"]), str(good["id"])])
    _drain(feeder)

    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "completed"
    assert (op["succeeded_count"], op["failed_count"]) == (1, 1)
    results = _results_by_job(job_db, op_id)
    assert results[str(bad["id"])]["reason_code"] == "internal_error"
    assert "simulated db hiccup" in results[str(bad["id"])]["message"]
    assert results[str(good["id"])]["status"] == "succeeded"
    chunk_errors = json.loads(op["chunk_errors_json"])
    assert len(chunk_errors) == 1 and "simulated db hiccup" in chunk_errors[0]["error"]
    assert job_db.get_job(str(good["id"])) is None


def test_watermark_gate_releases_work_creating_kinds(
    workspace, job_db, settings, monkeypatch
) -> None:
    job = _create_job(job_db, WORKSPACE, "W-1", status="failed")
    op_id = _submit(job_db, "rerun", [str(job["id"])], {"node_key": "write_script"})
    feeder = _feeder(job_db, settings)
    monkeypatch.setattr(feeder, "_watermark", lambda: 0)

    assert feeder.consume_once() is False
    assert job_db.get_job_batch_operation(op_id)["status"] == "pending"

    # 水位恢复后同一 feeder 能喂完。
    monkeypatch.setattr(feeder, "_watermark", lambda: 30_000)
    _drain(feeder)
    assert job_db.get_job_batch_operation(op_id)["status"] == "completed"

    # 不产生 backlog 的 kind 不受闸（rerun 已 completed，claim 只剩 pause）。
    monkeypatch.setattr(feeder, "_watermark", lambda: 0)
    pause_op = _submit(job_db, "pause", [str(job["id"])])
    _drain(feeder)
    assert job_db.get_job_batch_operation(pause_op)["status"] == "completed"


def test_watermark_gate_covers_upgrade_workflow(workspace, job_db, settings, monkeypatch) -> None:
    """clean 升级把 job 翻 queued、节点全重置 pending——与 rerun 同款的执行
    负载，必须在闸清单内（评审 P2）。"""
    job = _create_job(job_db, WORKSPACE, "W-upg", status="completed")
    op_id = _submit(job_db, "upgrade_workflow", [str(job["id"])], {"mode": "clean"})
    feeder = _feeder(job_db, settings)
    monkeypatch.setattr(feeder, "_watermark", lambda: 0)

    assert feeder.consume_once() is False  # 停喂，不执行
    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "pending" and op["next_index"] == 0

    monkeypatch.setattr(feeder, "_watermark", lambda: 30_000)
    _drain(feeder)
    assert job_db.get_job_batch_operation(op_id)["status"] == "completed"


def test_batch_level_failure_marks_failed(workspace, job_db, settings) -> None:
    job = _create_job(job_db, WORKSPACE, "B-1")
    op_id = _submit(job_db, "pause", [str(job["id"])])
    with job_db.connect() as conn:
        conn.execute("update job_batch_operations set target_spec_json='{' where id=%s", (op_id,))

    feeder = _feeder(job_db, settings)
    assert feeder.consume_once() is True

    op = job_db.get_job_batch_operation(op_id)
    assert op["status"] == "failed"
    assert op["finished_at"] is not None


def test_stale_running_claim_is_reclaimed(workspace, job_db, settings) -> None:
    job = _create_job(job_db, WORKSPACE, "S-1")
    op_id = _submit(job_db, "pause", [str(job["id"])])
    with job_db.connect() as conn:
        conn.execute(
            "update job_batch_operations set status='running',"
            " updated_at=current_timestamp - interval '1 hour' where id=%s",
            (op_id,),
        )

    _drain(_feeder(job_db, settings))

    assert job_db.get_job_batch_operation(op_id)["status"] == "completed"
    assert job_db.get_job(str(job["id"]))["execution_paused"]
