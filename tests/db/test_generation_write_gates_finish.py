"""EXEC-GENERATION-001 lease finish 写面：staged 文件提升与 events 后处理的
代次闸（#759 复审 P1-1/P2-1/P2-2）。

自 test_generation_write_gates.py 拆出（#779 codex 列车复审 P1-4——文件
超 800 拆分线，按写面拆成 upload/fanout/finish 三姊妹文件，用例零改动
迁移）。共享种子/同步工具见 tests/db/generation_write_gate_helpers.py。

#759 复审 P1-1（Worker 结果归档）：归档只解包到 staging 目录，文件提升经
``ExecutionResult.staged_file_moves`` 挤进 finish 的代次 CAS——迟到 finish
不再覆盖新现场的 job_dir；completion 镜像上传带 lease 过闸，旧代次零登记。
lease_lost 的正常收尾语义（runtime 置失败结果、finish CAS）不在本文件，
由 tests/executors/test_executor_runtime.py 钉住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.transaction import write_transaction
from server.app.executors import _artifact_promotion
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.jobs import JobQueries
from tests.db.generation_write_gate_helpers import _node_row, _seed_job, _seed_lease
from tests.postgres_support import TEST_DATABASE_URL

# ---------------------------------------------------------------------------
# #759 复审 P1-1：Worker 结果归档的文件提升只发生在 finish 代次闸内
# ---------------------------------------------------------------------------


def test_finish_promotes_staged_files_under_current_generation(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """对照组：代次一致时 finish 在闸内把 staged 文件提升进 job_dir，节点
    照常翻 completed。"""
    _seed_job(job_db, workspace_id="gate8-ws", job_id="gate8-job")
    _seed_lease(job_db, workspace_id="gate8-ws", job_id="gate8-job")
    job_dir = tmp_path / "jobdir"
    job_dir.mkdir()
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (staged_dir / "out.json").write_bytes(b"new-bytes")
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed",
            exit_code=0,
            staged_file_moves=((str(job_dir / "out.json"), str(staged_dir / "out.json")),),
        ),
    )

    assert ok is True
    assert (job_dir / "out.json").read_bytes() == b"new-bytes"
    assert _node_row("gate8-job", "node_a")["status"] == "completed"


def test_finish_skips_staged_files_when_generation_stale(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """reset bump 代次后迟到的 finish：节点翻转被 CAS 跳过（既有语义），
    staged 文件也绝不落盘——新现场的 job_dir 文件不被旧代次归档字节覆盖，
    staging 源文件未被消费（调用方负责清理）。"""
    _seed_job(job_db, workspace_id="gate9-ws", job_id="gate9-job")
    _seed_lease(job_db, workspace_id="gate9-ws", job_id="gate9-job")
    job_dir = tmp_path / "jobdir"
    job_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"current-generation-bytes")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (staged_dir / "out.json").write_bytes(b"stale-epoch-bytes")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("gate9-job",),
        )
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed",
            exit_code=0,
            staged_file_moves=((str(job_dir / "out.json"), str(staged_dir / "out.json")),),
        ),
    )

    assert ok is True  # lease 释放与 node_runs 历史行照常收尾
    assert (job_dir / "out.json").read_bytes() == b"current-generation-bytes"
    assert (staged_dir / "out.json").read_bytes() == b"stale-epoch-bytes"
    assert _node_row("gate9-job", "node_a")["status"] == "pending"


# ---------------------------------------------------------------------------
# #759 对抗复审 P2-2：批重放幂等
# ---------------------------------------------------------------------------


def test_file_moves_guarded_skips_already_promoted_on_replay(tmp_path: Path) -> None:
    """finish 批事务整批回滚重放：第一次尝试已把 source 移走，重放时
    source 缺席而 target 在场 = 已提升，按成功跳过且不破坏既有内容；
    source 与 target 都缺席才是真正的错误。"""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"already-promoted")

    guard = _artifact_promotion.promote_file_moves_guarded(
        [(job_dir / "out.json", staged_dir / "out.json")], backup_parent=job_dir
    )
    guard.discard()

    assert (job_dir / "out.json").read_bytes() == b"already-promoted"
    assert not list(job_dir.glob(".promote-rollback-*"))
    with pytest.raises(FileNotFoundError):
        _artifact_promotion.promote_file_moves_guarded(
            [(job_dir / "missing.json", staged_dir / "missing.json")], backup_parent=job_dir
        )


# ---------------------------------------------------------------------------
# #759 对抗复审 P2-1：stale finish 不做 events 后处理
# ---------------------------------------------------------------------------


def _record_events_post_processing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "server.app.services.token_usage_lease.capture_token_usage_after_lease_finish",
        lambda *args, **kwargs: calls.append("capture"),
    )
    monkeypatch.setattr(
        "shared.pi_events.compress_pi_events",
        lambda *args, **kwargs: calls.append("compress"),
    )
    return calls


def test_finish_runs_events_post_processing_under_current_generation(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照组：代次一致的 completed finish 照常跑 token capture + PI
    compression。"""
    calls = _record_events_post_processing(monkeypatch)
    _seed_job(job_db, workspace_id="gate12-ws", job_id="gate12-job")
    _seed_lease(job_db, workspace_id="gate12-ws", job_id="gate12-job")
    run_dir = tmp_path / "jobs" / "gate12-ws" / "gate12-job" / "runs" / "node_a"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed",
            exit_code=0,
            run_dir="jobs/gate12-ws/gate12-job/runs/node_a",
        ),
    )

    assert ok is True
    assert calls == ["capture", "compress"]


def test_finish_skips_events_post_processing_when_generation_stale(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reset bump 代次后迟到的 finish：run_dir 路径跨代次复用，token
    capture 会把新代次的 events 记到旧 run（双计）、PI compression 可能
    截断新 run 的 events.jsonl——stale 判定下 events 族整体跳过。"""
    calls = _record_events_post_processing(monkeypatch)
    _seed_job(job_db, workspace_id="gate13-ws", job_id="gate13-job")
    _seed_lease(job_db, workspace_id="gate13-ws", job_id="gate13-job")
    run_dir = tmp_path / "jobs" / "gate13-ws" / "gate13-job" / "runs" / "node_a"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text('{"new":"generation"}\n', encoding="utf-8")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("gate13-job",),
        )
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed",
            exit_code=0,
            run_dir="jobs/gate13-ws/gate13-job/runs/node_a",
        ),
    )

    assert ok is True
    assert calls == []
    assert (run_dir / "events.jsonl").read_text(encoding="utf-8") == '{"new":"generation"}\n'
