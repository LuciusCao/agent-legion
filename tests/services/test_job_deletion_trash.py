"""#958：job 删除 ``.trash`` 的 TTL 回收语义（纯文件系统，不碰 DB）。"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from server.app.services.job_deletion_trash import (
    COMMITTED_MARKER,
    DELETION_TRASH_TTL,
    jobs_trash_root,
    logs_trash_root,
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
