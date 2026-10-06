"""#958：job 删除 ``.trash`` 的 TTL 回收语义（纯文件系统，不碰 DB）。"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import server.app.services.job_deletion_trash as trash_module
from server.app.services.job_deletion_trash import (
    COMMITTED_MARKER,
    DELETION_TRASH_TTL,
    jobs_trash_root,
    logs_trash_root,
    purge_deleted_job_files,
)
from server.app.services.job_deletion_trash_sweep import sweep_deletion_trash
from server.app.settings import Settings
from server.app.workflow_worker.maintenance import WorkflowMaintenance

pytestmark = pytest.mark.no_db

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
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


def _op_dir(
    root: Path, name: str, age: timedelta, child: str = "payload", *, committed: bool = True
) -> Path:
    op = root / name
    (op / child).mkdir(parents=True)
    (op / child / "f.bin").write_bytes(b"x")
    if committed:
        (op / COMMITTED_MARKER).write_text("job\n", encoding="utf-8")
    stamp = (_NOW - age).timestamp()
    os.utime(op, (stamp, stamp))
    return op


def test_sweep_removes_only_entries_older_than_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    old_job = _op_dir(jobs_trash_root(settings), "op-old", DELETION_TRASH_TTL + timedelta(hours=1))
    young_job = _op_dir(jobs_trash_root(settings), "op-young", timedelta(minutes=5))
    old_log = _op_dir(logs_trash_root(settings), "op-old", DELETION_TRASH_TTL * 2)

    removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 2
    assert not old_job.exists()
    assert not old_log.exists()
    assert (young_job / "payload" / "f.bin").exists()


def test_sweep_never_purges_legacy_unmarked_rollback_copies(tmp_path: Path) -> None:
    """P1：0.7.17 前 _restore_paths 回滚冲突留下的恢复副本无已提交标记，其
    jobs 行仍在、可能是 legacy job 唯一的产物副本——超 TTL 也不自动删除。"""
    settings = _settings(tmp_path)
    legacy = _op_dir(
        jobs_trash_root(settings),
        "old-operation",
        DELETION_TRASH_TTL * 10,
        child="live-job",
        committed=False,
    )
    legacy_log = _op_dir(
        logs_trash_root(settings), "old-operation", DELETION_TRASH_TTL * 10, committed=False
    )
    marked = _op_dir(jobs_trash_root(settings), "op-new", DELETION_TRASH_TTL * 2)

    removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 1
    assert not marked.exists()
    assert (legacy / "live-job" / "f.bin").exists()
    assert (legacy_log / "payload" / "f.bin").exists()


def test_sweep_never_touches_symlink_entries(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    root = jobs_trash_root(settings)
    root.mkdir(parents=True)
    link = root / "op-link"
    link.symlink_to(outside, target_is_directory=True)
    stamp = (_NOW - DELETION_TRASH_TTL * 2).timestamp()
    os.utime(link, (stamp, stamp), follow_symlinks=False)

    (outside / COMMITTED_MARKER).write_text("job\n", encoding="utf-8")

    assert sweep_deletion_trash(settings, now=_NOW) == 0

    assert link.is_symlink()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_sweep_without_trash_roots_is_noop(tmp_path: Path) -> None:
    assert sweep_deletion_trash(_settings(tmp_path), now=_NOW) == 0


def test_sweep_survives_single_entry_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    age = DELETION_TRASH_TTL * 2
    first = _op_dir(jobs_trash_root(settings), "op-a", age)
    second = _op_dir(jobs_trash_root(settings), "op-b", age)
    real_rmtree = shutil.rmtree

    def _flaky(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) == first:
            raise OSError("busy")
        real_rmtree(path, *args, **kwargs)

    with patch("server.app.services.job_deletion_trash_sweep.shutil.rmtree", side_effect=_flaky):
        removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 1
    assert first.exists()
    assert not second.exists()


def test_maintenance_runs_trash_sweep(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.config = {}
    maintenance = WorkflowMaintenance(MagicMock(), settings)

    with (
        patch("server.app.workflow_worker.maintenance.cleanup_old_logs", return_value=(0, 0)),
        patch(
            "server.app.workflow_worker.maintenance.sweep_deletion_trash", return_value=3
        ) as sweep,
    ):
        maintenance._run_cleanup()

    sweep.assert_called_once_with(settings)


def test_purge_enumerates_log_paths_outside_job_mutation_lock(tmp_path: Path) -> None:
    """#1065 codex P2：日志 glob 在锁外枚举，锁内只复核行并 rename。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    (log_dir / "job-x-n1.log").write_text("log", encoding="utf-8")
    (log_dir / "job-x-n1-shard-0.log").write_text("shard", encoding="utf-8")
    in_lock = False
    globbed_in_lock: list[bool] = []

    class _JobDB:
        @contextmanager
        def job_mutation_lock(self, job_id: str) -> Any:
            nonlocal in_lock
            in_lock = True
            try:
                yield False
            finally:
                in_lock = False

    real_glob = trash_module.glob.glob

    def _spy_glob(pattern: str) -> list[str]:
        globbed_in_lock.append(in_lock)
        return real_glob(pattern)

    with patch.object(trash_module.glob, "glob", _spy_glob):
        recreated = purge_deleted_job_files(
            _JobDB(), {"id": "job-x", "storage_dir": "job-x"}, ["n1"], settings, "op-1"
        )

    assert recreated is False
    assert globbed_in_lock == [False]
    assert sorted(p.name for p in log_dir.iterdir()) == []


def test_purge_reports_recreated_when_lock_fails_before_recheck(tmp_path: Path) -> None:
    """#1065 codex P2：锁事务在给出存在性结果前失败（瞬时 DB 错误）时，无法
    排除同源重建，必须按「已重建」返回，让调用方跳过按 id 的清理与广播。"""
    settings = _settings(tmp_path)
    job_dir = settings.jobs_dir / "job-y"
    job_dir.mkdir(parents=True)

    class _JobDB:
        @contextmanager
        def job_mutation_lock(self, job_id: str) -> Any:
            raise RuntimeError("connection lost")
            yield False  # pragma: no cover

    recreated = purge_deleted_job_files(
        _JobDB(), {"id": "job-y", "storage_dir": "job-y"}, [], settings, "op-1"
    )

    assert recreated is True
    assert job_dir.is_dir()


def test_purge_keeps_recheck_result_when_lock_fails_after_yield(tmp_path: Path) -> None:
    """yield 之后（如 commit）才失败：复核值已产出，照常返回真实结果。"""
    settings = _settings(tmp_path)

    class _JobDB:
        @contextmanager
        def job_mutation_lock(self, job_id: str) -> Any:
            yield False
            raise RuntimeError("commit failed")

    recreated = purge_deleted_job_files(
        _JobDB(), {"id": "job-z", "storage_dir": "job-z"}, [], settings, "op-1"
    )

    assert recreated is False
