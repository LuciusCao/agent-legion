"""#1357（#1248 删除广播面）：删除广播对同源重建新 job 的隔离（自
test_job_deletion_service.py 按主题拆出——文件体积纪律，夹具为同形副本）。

purge 的 ``job-mutation:<id>`` 锁在 rename 提交后即释放；广播若按裸 id 直发，
落在「锁释放 → 广播」之间提交的同源重建（确定性 id）会让客户端把**新** job
当作已删除移除。修复与 refs gc 面（``delete_refs_for_job`` 的 not exists
条件，#1248）同族：广播在 ``job-mutation:<id>`` 锁下复核 jobs 行仍不存在才
发出（创建持同锁到提交，#1083）——重建要么已落行（复核命中 → 跳过），要么
等锁释放后才提交、其 created 事件序在 deleted 之后。复核因 DB 错误失败时
无法排除重建，保守跳过广播。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import server.app.services.job_deletion as job_deletion_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_deletion import JobDeletionService
from server.app.settings import Settings


def _create_settings(tmp_path: Path) -> Settings:
    # Match the paths produced by the job_db fixture, which uses
    # load_settings(data_dir=tmp_path) and therefore jobs_dir=tmp_path/jobs.
    data_dir = tmp_path
    jobs_dir = data_dir / "jobs"
    logs_dir = data_dir / "logs"
    videos_dir = data_dir / "videos"
    packages_dir = data_dir / "packages"
    for path in [data_dir, jobs_dir, logs_dir, videos_dir, packages_dir]:
        path.mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config={},
    )


def _create_job(
    job_db: JobQueries,
    workspace_id: str,
    source_id: str,
    status: str = "queued",
) -> dict[str, Any]:
    job_db.create_workspace(workspace_id)
    batch = job_db.create_run(
        "demo_workflow", "batch_by_ids", {"question_ids": [source_id]}, workspace_id
    )
    job = job_db.create_job(
        "demo_workflow",
        "question",
        source_id,
        batch["id"],
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    if status != "queued":
        job_db.update_job_status(job["id"], status)
    return job


class _RecordingEventManager:
    """JobEventManager.broadcast_job_deleted 的最小录制替身。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def broadcast_job_deleted(self, workspace_id: str, job_id: str, stats: dict[str, int]) -> None:
        self.deleted.append(job_id)


class _RecordingEventBuffer:
    """JobEventBuffer.record_job_deleted 的最小录制替身。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def record_job_deleted(self, workspace_id: str, job_id: str) -> int:
        self.deleted.append(job_id)
        return len(self.deleted)


def _recreate_same_source_job(job_db: JobQueries, workspace_id: str, source_id: str) -> None:
    """同源重建（确定性 id）：新建 run + 同 source_id 的 job 行。"""
    batch = job_db.create_run(
        "demo_workflow", "batch_by_ids", {"question_ids": [source_id]}, workspace_id
    )
    job_db.create_job(
        "demo_workflow",
        "question",
        source_id,
        batch["id"],
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )


@pytest.mark.parametrize("sink", ["manager", "buffer"])
def test_delete_broadcast_skipped_when_job_recreated_after_cleanup_lock(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: str
) -> None:
    """#1357：purge 的锁释放后、删除广播前同源 job 重建（确定性 id）时，广播
    必须跳过——否则客户端把**新** job 当作已删除移除。

    本用例在 gc seam 里重建（此刻删除事务与 purge 锁都已释放、广播未发——
    正是 issue 的窗口）：锁下复核命中新行 → 两种事件汇（内存 manager / 缓冲
    buffer）都不得记录该 id。突变自检：去掉锁下复核直接广播 → 本用例红。"""
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    manager = _RecordingEventManager()
    buffer = _RecordingEventBuffer()
    service = JobDeletionService(
        job_db,
        lease_repo,
        settings,
        job_event_manager=manager if sink == "manager" else None,
        job_event_buffer=buffer if sink == "buffer" else None,
    )
    job = _create_job(job_db, "ws-1357", "Q1357", status="queued")

    real_gc = job_deletion_module.gc_deleted_job_artifacts

    def _recreate_inside_gc(store: Any, job_id: str, candidates: list[str]) -> None:
        # 删除已提交、purge 的锁已释放、广播未发——窗口内同源重建。
        _recreate_same_source_job(job_db, "ws-1357", "Q1357")
        real_gc(store, job_id, candidates)

    monkeypatch.setattr(job_deletion_module, "gc_deleted_job_artifacts", _recreate_inside_gc)

    result = service.delete("ws-1357", job["id"])

    assert result["status"] == "succeeded"
    recreated = job_db.get_job(job["id"])
    assert recreated is not None, "the same-source recreate must have landed"
    assert manager.deleted == [] and buffer.deleted == [], (
        "deletion broadcast must not hit the recreated job"
    )


def test_delete_broadcast_skipped_when_identity_recheck_fails(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1357 保守臂：广播前的锁下身份复核因 DB 错误未能给出结果时，无法排除
    同源重建——跳过广播（泄漏一个列表残留，客户端下次全量刷新自愈），但删除
    本身仍判成功（行已提交删除）。"""
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    manager = _RecordingEventManager()
    service = JobDeletionService(job_db, lease_repo, settings, job_event_manager=manager)
    job = _create_job(job_db, "ws-1357-err", "Q1357", status="queued")

    # purge 的清理复核用第一把锁（须正常，否则 purge 保守臂会提前跳过广播
    # 路径）；注入的失败只打广播复核用的第二把锁。
    real_lock = job_db.job_mutation_lock
    calls = {"n": 0}

    @contextmanager
    def _failing_lock(job_id: str) -> Any:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("injected lock failure")
        with real_lock(job_id) as recreated:
            yield recreated

    monkeypatch.setattr(job_db, "job_mutation_lock", _failing_lock)

    result = service.delete("ws-1357-err", job["id"])

    assert result["status"] == "succeeded"
    assert job_db.get_job(job["id"]) is None
    assert manager.deleted == []


@pytest.mark.parametrize("sink", ["manager", "buffer"])
def test_delete_broadcast_still_emitted_without_recreate(
    job_db: JobQueries, tmp_path: Path, sink: str
) -> None:
    """#1357 对照组：无重建时锁下复核通过、删除广播照常发出（两种事件汇）。"""
    settings = _create_settings(tmp_path)
    lease_repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    manager = _RecordingEventManager()
    buffer = _RecordingEventBuffer()
    service = JobDeletionService(
        job_db,
        lease_repo,
        settings,
        job_event_manager=manager if sink == "manager" else None,
        job_event_buffer=buffer if sink == "buffer" else None,
    )
    job = _create_job(job_db, "ws-1357-ok", "Q1357", status="queued")

    result = service.delete("ws-1357-ok", job["id"])

    assert result["status"] == "succeeded"
    expected = [job["id"]] if sink == "manager" else []
    expected_buffer = [job["id"]] if sink == "buffer" else []
    assert manager.deleted == expected
    assert buffer.deleted == expected_buffer
