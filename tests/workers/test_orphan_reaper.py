"""Regression tests for orphaned agent process-group reaping.

The executor spawns agents with start_new_session=True (own process group)
and records the pid (= pgid) in the execution dir. If the executor is
SIGKILLed it cannot run its own cleanup, so the supervisor reaps the
recorded groups after killing the executor.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from worker.process_lifecycle import AGENT_PGID_FILENAME, reap_orphaned_agents

pytestmark = pytest.mark.no_db


def _spawn_group() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )


def _write_record(work_root: Path, execution_id: str, pid: int | str) -> Path:
    record_dir = work_root / execution_id
    record_dir.mkdir(parents=True)
    record = record_dir / AGENT_PGID_FILENAME
    record.write_text(str(pid), encoding="utf-8")
    return record


def test_reap_kills_recorded_process_group(tmp_path: Path) -> None:
    proc = _spawn_group()
    record = _write_record(tmp_path, "exec-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.poll() is not None
    assert not record.exists()


def test_reap_kills_grandchildren_too(tmp_path: Path) -> None:
    """The whole group dies, not just the direct child (no orphaned grandchildren)."""
    marker = tmp_path / "grandchild-survived"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, time\n"
            f"subprocess.Popen(['/bin/sh', '-c', 'sleep 2; touch {marker}'])\n"
            "time.sleep(60)\n",
        ],
        start_new_session=True,
    )
    _write_record(tmp_path, "exec-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    time.sleep(2.5)
    assert not marker.exists()


def test_reap_ignores_garbage_and_esrch_records(tmp_path: Path) -> None:
    garbage = _write_record(tmp_path, "exec-1", "not-a-pid")
    stale = _write_record(tmp_path, "exec-2", 999_999_999)  # ESRCH

    reap_orphaned_agents(tmp_path)  # must not raise

    assert garbage.exists()  # 解析失败的记录保留，下次 stop 再试
    assert not stale.exists()  # ESRCH 忽略，记录正常清除


def test_reap_tolerates_empty_work_root(tmp_path: Path) -> None:
    reap_orphaned_agents(tmp_path / "missing")  # must not raise
    reap_orphaned_agents(tmp_path)
