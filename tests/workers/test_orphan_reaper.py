"""Regression tests for orphaned agent process-group reaping.

The executor spawns agents with start_new_session=True (own process group)
and records the pid (= pgid) in the execution dir. If the executor is
SIGKILLed it cannot run its own cleanup, so the supervisor reaps the
recorded groups after killing the executor.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from worker.orphan_reaper import reap_orphaned_agents
from worker.process_lifecycle import AGENT_PGID_FILENAME

pytestmark = pytest.mark.no_db


def _spawn_group(marker: str = "") -> subprocess.Popen[bytes]:
    code = "import time; time.sleep(60)"
    if marker:
        # argv 携带 agent 标记，模拟真实 agent 命令（--name agent-legion-<execution_id>）
        code += f"  # {marker}"
    return subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
    )


def _write_record(work_root: Path, execution_id: str, pid: int | str) -> Path:
    record_dir = work_root / execution_id
    record_dir.mkdir(parents=True)
    record = record_dir / AGENT_PGID_FILENAME
    record.write_text(str(pid), encoding="utf-8")
    return record


def test_reap_kills_recorded_process_group(tmp_path: Path) -> None:
    proc = _spawn_group(marker="agent-legion-exec-1")
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


def test_reap_skips_group_without_agent_marker(tmp_path: Path) -> None:
    """pgid 被 OS 复用后组内没有本 execution 的 agent 标记：不得误杀，只清记录。"""
    proc = _spawn_group()  # 无标记 —— 冒充复用了 pgid 的无关进程组
    record = _write_record(tmp_path, "exec-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        assert proc.poll() is None  # 未收到任何信号
        assert not record.exists()
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_reap_kills_grandchildren_too(tmp_path: Path) -> None:
    """The whole group dies, not just the direct child (no orphaned grandchildren)."""
    marker = tmp_path / "grandchild-survived"
    pid_file = tmp_path / "grandchild.pid"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, time\n"
            f"g = subprocess.Popen(['/bin/sh', '-c', 'sleep 2; touch {marker}'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
            "time.sleep(60)  # agent-legion-exec-1\n",
        ],
        start_new_session=True,
    )
    _write_record(tmp_path, "exec-1", proc.pid)
    try:
        # The pid file must exist BEFORE reaping: the reaper kills the whole
        # group on sight, and racing it against the grandchild's spawn (as CI
        # just proved) makes the file never appear. Waiting here also proves
        # the grandchild actually started, which is what the kill must cover.
        _wait_for_file(pid_file, timeout=10)
        grandchild = int(pid_file.read_text().strip())
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
        # Watch the grandchild's PID exit instead of sleeping past its touch
        # deadline: once the PID is gone, a surviving shell can no longer
        # touch. Race-free under load, and ~2s faster than the old blind wait.
        deadline = time.monotonic() + 10.0
        while _pid_alive(grandchild):
            assert time.monotonic() < deadline, (
                f"grandchild {grandchild} survived the reaper; marker={marker}"
            )
            time.sleep(0.05)
    finally:
        # Group-targeted cleanup: killing the grandchild by PID after its own
        # death could hit a recycled PID; the pgid (== proc.pid, established by
        # start_new_session and still ours while the group has members) cannot
        # be misattributed.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        proc.wait(timeout=5)

    assert not marker.exists()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_file(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)


def test_reap_ignores_garbage_and_esrch_records(tmp_path: Path) -> None:
    garbage = _write_record(tmp_path, "exec-1", "not-a-pid")
    stale = _write_record(tmp_path, "exec-2", 999_999_999)  # ESRCH

    reap_orphaned_agents(tmp_path)  # must not raise

    assert garbage.exists()  # 解析失败的记录保留，下次 stop 再试
    assert not stale.exists()  # ESRCH 忽略，记录正常清除


@pytest.mark.parametrize("bad_pgid", ["0", "1", "-3"])
def test_reap_rejects_dangerous_pgid_records(tmp_path: Path, bad_pgid: str) -> None:
    """pgid <= 1 的记录（半截/恶意写入）会把信号发给调用方自身进程组，
    必须按垃圾记录跳过：不发送信号、不崩溃、记录保留待人工检查。"""
    record = _write_record(tmp_path, "exec-1", bad_pgid)

    reap_orphaned_agents(tmp_path)  # must not raise, must not signal our own group

    assert record.exists()
    os.killpg(os.getpgrp(), 0)  # 我们所在进程组安然无恙


def test_reap_tolerates_empty_work_root(tmp_path: Path) -> None:
    reap_orphaned_agents(tmp_path / "missing")  # must not raise
    reap_orphaned_agents(tmp_path)
