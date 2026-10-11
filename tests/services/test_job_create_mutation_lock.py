"""#1083：job 创建进入 ``job-mutation`` 锁域（#1065 follow-up）。

删除的提交后清理在 ``job-mutation:<id>`` 锁下复核 jobs 行不存在，再把 job_dir
rename 入 ``.trash``。创建路径（``create_job`` / ``create_jobs_bulk``）此前不取
锁且先 mkdir 再 upsert：同源重建若落在「复核 → rename」之间，它采用的目录会被
整体移走。现在创建在 mkdir / upsert 前取同一把锁并持到提交。

同步纪律（等信号不等时长）：竞态点用 monkeypatch 嵌进真实路径（清理锁内的
``_stage``），创建方在另一线程发起，经 pg_locks 观测「它正等待该 advisory
锁」后才放行；join 带超时并断言线程已结束，防假绿。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

import server.app.services.job_deletion_trash as trash_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_deletion import JobDeletionService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir

_WORKFLOW = "demo_workflow"
_REVISION = {"id": "rev-1083", "version": 1, "definition_hash": "h", "definition_json": "{}"}


def _settings(tmp_path: Path) -> Settings:
    # 与 job_db fixture 的路径一致（load_settings(data_dir=tmp_path) → tmp_path/jobs）。
    for name in ["jobs", "logs", "videos", "packages"]:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )


def _new_run(job_db: JobQueries, workspace_id: str, source_ids: list[str]) -> str:
    batch = job_db.create_run(_WORKFLOW, "batch_by_ids", {"question_ids": source_ids}, workspace_id)
    return str(batch["id"])


def _create_single(job_db: JobQueries, workspace_id: str, source_id: str) -> str:
    job = job_db.create_job(
        _WORKFLOW,
        "question",
        source_id,
        _new_run(job_db, workspace_id, [source_id]),
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _create_bulk(job_db: JobQueries, workspace_id: str, source_ids: list[str]) -> list[str]:
    return job_db.create_jobs_bulk(
        candidates=[
            {"entity_id": sid, "entity_type": "question", "title": sid, "stem": sid}
            for sid in source_ids
        ],
        workflow_key=_WORKFLOW,
        run_id=_new_run(job_db, workspace_id, source_ids),
        node_keys=["extract_question"],
        workspace_id=workspace_id,
        revision=_REVISION,
    )


def _advisory_key(job_db: JobQueries, job_id: str) -> int:
    with job_db.read() as conn:
        row = conn.execute("select hashtext(%s) as k", (f"job-mutation:{job_id}",)).fetchone()
    assert row is not None
    return int(row["k"]) & 0xFFFFFFFFFFFFFFFF


def _advisory_holders(job_db: JobQueries, job_id: str, *, granted: bool) -> list[int]:
    """持有（granted）或等待（not granted）该 job-mutation 锁的 backend pid。

    单键 advisory 锁（objsubid=1）在 pg_locks 里拆成 (classid, objid) 两段 32 位，
    按无符号 64 位还原后与 hashtext 比对（同 tests/db 的 generation 竞态测试）。
    """
    expected = _advisory_key(job_db, job_id)
    with job_db.read() as conn:
        rows = conn.execute(
            "select classid, objid, pid from pg_locks"
            " where locktype='advisory' and objsubid=1 and granted=%s",
            (granted,),
        ).fetchall()
    return [
        int(row["pid"])
        for row in rows
        if ((int(row["classid"]) << 32) | int(row["objid"])) & 0xFFFFFFFFFFFFFFFF == expected
    ]


def _await_waiter(job_db: JobQueries, job_id: str, timeout: float = 10.0) -> list[int]:
    """等到有 backend 正等待该 job 的 job-mutation 锁；返回等待者 pid（超时为空）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiters = _advisory_holders(job_db, job_id, granted=False)
        if waiters:
            return waiters
        time.sleep(0.02)
    return []


def _start(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:  # 线程内异常交回主线程断言
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive(), "creating transaction never resolved"


@pytest.mark.parametrize("path", ["create_job", "create_jobs_bulk"])
def test_recreate_inside_cleanup_recheck_window_waits_and_keeps_its_dir(
    job_db: JobQueries, tmp_path: Path, monkeypatch, path: str
) -> None:
    """#1065 R1 窗口：清理已在锁下复核「行不存在」、尚未 rename 时同源重建。

    重建必须阻塞在 job-mutation 锁上（不得先 mkdir / upsert），rename 把旧目录
    移走后才落地：新 job 有行、有自己的空目录，旧 job 的文件不会被它继承，
    也不会连同新 job 的目录一起被移入 trash。
    """
    settings = _settings(tmp_path)
    workspace_id = f"ws-1083-{path.replace('_', '-')}"
    job_db.create_workspace(workspace_id)
    job_id = _create_single(job_db, workspace_id, "Q1083")
    job = job_db.get_job(job_id)
    assert job is not None
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    (storage_dir / "old.json").write_text("old", encoding="utf-8")
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )

    def recreate() -> Any:
        if path == "create_job":
            return _create_single(job_db, workspace_id, "Q1083")
        return _create_bulk(job_db, workspace_id, ["Q1083"])

    original_stage = trash_module._stage
    observed: dict[str, Any] = {}

    def stage_after_racing_recreate(target: Path, *args: Any, **kwargs: Any) -> list[Path]:
        if target == storage_dir and "thread" not in observed:
            # 此刻：删除已提交、清理持锁并已复核行不存在、rename 尚未发生。
            observed["thread"], observed["outcome"] = _start(recreate)
            observed["waiters"] = _await_waiter(job_db, job_id)
            observed["row_while_blocked"] = job_db.get_job(job_id)
        return original_stage(target, *args, **kwargs)

    monkeypatch.setattr(trash_module, "_stage", stage_after_racing_recreate)

    result = service.delete(workspace_id, job_id)

    _join(observed["thread"])
    assert "error" not in observed["outcome"], observed["outcome"].get("error")
    assert result["status"] == "succeeded"
    assert observed["waiters"], "recreate did not wait on job-mutation inside the window"
    assert observed["row_while_blocked"] is None
    recreated = job_db.get_job(job_id)
    assert recreated is not None
    assert resolve_job_dir(recreated, settings.jobs_dir) == storage_dir
    assert storage_dir.is_dir(), "recreated job's directory was moved into .trash"
    assert list(storage_dir.iterdir()) == []


def test_bulk_create_rejects_cross_workspace_id_collision_before_locking(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """#1243 codex P2：id 由带下划线的字段拼接，可能撞上别的 workspace 的现存 job。
    批路径按该 job 自己的 workspace 键排序，按本 workspace 的 job_id 序去锁它
    可能 AB-BA；所以碰撞必须在取任何 job-mutation 锁之前以结构化 identity 错误
    拒绝——即便该 job 的锁正被别人持有，创建也不等待、不建目录、不写行。"""
    own_ws = "w1243"
    foreign_ws = f"{own_ws}_{_WORKFLOW}_x"
    colliding_source = f"x_{_WORKFLOW}_y"
    job_db.create_workspace(own_ws)
    job_db.create_workspace(foreign_ws)
    foreign_id = _create_single(job_db, foreign_ws, "y")
    assert foreign_id == f"{own_ws}_{_WORKFLOW}_{colliding_source}"
    other_id = f"{own_ws}_{_WORKFLOW}_zz"

    with job_db.job_mutation_lock(foreign_id) as exists:
        assert exists is True
        thread, outcome = _start(lambda: _create_bulk(job_db, own_ws, [colliding_source, "zz"]))
        thread.join(timeout=30)
        assert not thread.is_alive(), "bulk create waited on a foreign job's lock"
        assert _advisory_holders(job_db, foreign_id, granted=False) == []

    assert isinstance(outcome.get("error"), ValueError), outcome
    assert "collision" in str(outcome["error"]).lower()
    assert job_db.get_job(other_id) is None
    assert not any((tmp_path / "jobs").rglob(other_id))


def _lock_under_sort(node: dict[str, Any], sort_above: bool = False) -> bool:
    """True if some plan node evaluating pg_advisory_xact_lock has a Sort ancestor."""
    evaluates = any("pg_advisory_xact_lock" in out for out in node.get("Output", []))
    if evaluates and sort_above:
        return True
    below = sort_above or node.get("Node Type") == "Sort"
    return any(_lock_under_sort(child, below) for child in node.get("Plans", []))


def test_set_based_lock_statement_never_sorts_after_acquiring(job_db: JobQueries) -> None:
    """#1243 codex R2：一条集合语句取完全部锁，顺序必须是数组（已排序）顺序。

    规划器要么因 ordinality 已有序而省掉 Sort、按扫描顺序逐行求值，要么把
    volatile 目标列推到 Sort 之上求值；任何「先取锁再排序」的计划都会让取锁
    顺序退化为扫描顺序以外的东西——在此钉住。真实取锁顺序另由下方 pg_locks
    用例覆盖。"""
    from server.app.jobs.queries.job_bulk_sql import LOCK_JOB_MUTATIONS_SQL

    with job_db.connect() as conn:
        plan = conn.execute(
            "explain (verbose, format json) " + LOCK_JOB_MUTATIONS_SQL, (["c", "a", "b"],)
        ).fetchone()
    assert plan is not None
    root = next(iter(plan.values()))[0]["Plan"]
    assert "pg_advisory_xact_lock" in str(root)
    assert not _lock_under_sort(root), root


def test_bulk_create_takes_job_mutation_locks_in_job_id_order(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """锁序防死锁：bulk chunk 按 job_id 升序取锁（全局批序在单 workspace 上的
    退化形态），与输入顺序无关。中间 id 的锁被占住时，bulk 已持有更小的 id、
    尚未碰更大的 id，且尚未建任何目录或写任何行。"""
    workspace_id = "ws-1083-order"
    job_db.create_workspace(workspace_id)
    ids = {sid: f"{workspace_id}_{_WORKFLOW}_{sid}" for sid in ("a", "b", "c")}

    with job_db.job_mutation_lock(ids["b"]) as exists:
        assert exists is False
        thread, outcome = _start(lambda: _create_bulk(job_db, workspace_id, ["c", "a", "b"]))
        waiters = _await_waiter(job_db, ids["b"])
        assert waiters, "bulk create never waited on the held job-mutation lock"
        assert _advisory_holders(job_db, ids["a"], granted=True) == waiters
        assert _advisory_holders(job_db, ids["c"], granted=True) == []
        assert [sid for sid, jid in ids.items() if job_db.get_job(jid) is not None] == []
        assert not any((tmp_path / "jobs").rglob(f"*{_WORKFLOW}_*"))

    _join(thread)
    assert "error" not in outcome, outcome.get("error")
    assert sorted(outcome["result"]) == sorted(ids.values())
    for job_id in ids.values():
        job = job_db.get_job(job_id)
        assert job is not None
        assert resolve_job_dir(job, tmp_path / "jobs").is_dir()
