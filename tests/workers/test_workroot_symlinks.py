"""Symlink handling in work-root hygiene (worker/cleanup.py, worker/stale_sweep.py).

is_dir() follows symlinks: rmtree on a symlinked dir either errors or — worse —
could be aimed outside the work root. Symlinks must be unlinked, never followed.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from worker.cleanup import clean_work_root
from worker.stale_sweep import sweep_stale_executions

pytestmark = pytest.mark.no_db


def _outside_dir(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    return outside


def test_clean_work_root_unlinks_symlinked_dirs(tmp_path: Path) -> None:
    outside = _outside_dir(tmp_path)
    work_root = tmp_path / "work"
    work_root.mkdir()
    (work_root / "exec-link").symlink_to(outside, target_is_directory=True)

    clean_work_root(work_root)

    assert not (work_root / "exec-link").is_symlink()
    assert (outside / "keep.txt").is_file()  # rmtree 不得触及链接目标


def test_sweep_unlinks_symlink_without_following(tmp_path: Path) -> None:
    outside = _outside_dir(tmp_path)
    work_root = tmp_path / "work"
    work_root.mkdir()
    (work_root / "exec-link").symlink_to(outside, target_is_directory=True)

    sweep_stale_executions(work_root, max_age_seconds=0)

    assert not (work_root / "exec-link").is_symlink()
    assert (outside / "keep.txt").is_file()


def test_sweep_ignores_outside_mtime_through_symlink(tmp_path: Path) -> None:
    """A fresh external target must not keep a stale execution dir alive."""
    outside = _outside_dir(tmp_path)  # fresh mtime
    work_root = tmp_path / "work"
    child = work_root / "exec-1"
    child.mkdir(parents=True)
    link = child / "link"
    link.symlink_to(outside / "keep.txt")
    old = time.time() - 48 * 3600
    os.utime(child, (old, old))
    os.utime(link, (old, old), follow_symlinks=False)

    sweep_stale_executions(work_root, max_age_seconds=24 * 3600)

    assert not child.exists()  # 链接目标的 mtime 不再给过期目录续命
    assert (outside / "keep.txt").is_file()


def test_sweep_tolerates_dangling_symlink_in_subtree(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    child = work_root / "exec-1"
    child.mkdir(parents=True)
    (child / "dangling").symlink_to(tmp_path / "missing-target")

    sweep_stale_executions(work_root, max_age_seconds=0)

    assert not child.exists()
