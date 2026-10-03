"""PID 1 zombie reaping + /proc readers (#682).

The containerized Worker Service is PID 1; orphans of a SIGKILLed executor are
reparented to it. The reaper must collect them without ever stealing the exit
status of the supervisor's own Popen children (Popen.wait would then report a
fabricated returncode 0).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import worker.supervisor as supervisor_module
from worker import procfs
from worker.config_store import validate_config
from worker.supervisor import WorkerConfigStore, WorkerSupervisor
from worker.zombie_reaper import (
    REAP_LOCK,
    ManagedChildren,
    ZombieReaper,
    collect_group,
    reaping_enabled,
)

pytestmark = pytest.mark.no_db

linux_proc = pytest.mark.skipif(
    not Path("/proc/self/stat").is_file(), reason="needs Linux /proc (macOS has none)"
)


def _stat_line(pid: int, comm: str, state: str, ppid: int, pgid: int, sid: int, start: int) -> str:
    # fields 7..21 are irrelevant here; starttime is field 22.
    middle = " ".join(["0"] * 15)
    return f"{pid} ({comm}) {state} {ppid} {pgid} {sid} {middle} {start} 0 0\n"


def _fake_proc(
    root: Path,
    pid: int,
    *,
    state: str = "S",
    ppid: int = 1,
    pgid: int | None = None,
    sid: int | None = None,
    start: int = 100,
    cmdline: list[str] | None = None,
    comm: str = "bwrap",
) -> None:
    entry = root / str(pid)
    entry.mkdir(parents=True)
    entry.joinpath("stat").write_text(
        _stat_line(pid, comm, state, ppid, pgid or pid, sid or pid, start), encoding="utf-8"
    )
    argv = cmdline if cmdline is not None else ([] if state == "Z" else [comm])
    entry.joinpath("cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))


def _children_file(root: Path, parent: int, children: list[int], tid: int | None = None) -> None:
    task = root / str(parent) / "task" / str(tid or parent)
    task.mkdir(parents=True, exist_ok=True)
    task.joinpath("children").write_text(" ".join(map(str, children)) + " ", encoding="utf-8")


def test_parse_stat_handles_comm_with_spaces_and_parens() -> None:
    stat = procfs.parse_stat(_stat_line(42, "evil ) (comm", "Z", 1, 40, 39, 777))

    assert stat == procfs.ProcStat(pid=42, state="Z", ppid=1, pgid=40, sid=39, starttime=777)
    assert procfs.parse_stat("garbage") is None
    assert procfs.parse_stat("12 (x) S 1") is None


def test_read_cmdline_joins_nul_separated_argv(tmp_path: Path) -> None:
    _fake_proc(tmp_path, 7, cmdline=["velites", "--name", "agent-legion-exec-1"])

    assert procfs.read_cmdline(7, tmp_path) == "velites --name agent-legion-exec-1"
    assert procfs.read_cmdline(8, tmp_path) == ""  # vanished process: empty, no raise


class _FakeWait:
    def __init__(self, raising: frozenset[int] = frozenset()) -> None:
        self.calls: list[int] = []
        self._raising = raising

    def __call__(self, pid: int, options: int) -> tuple[int, int]:
        assert options == os.WNOHANG
        self.calls.append(pid)
        if pid in self._raising:
            raise ChildProcessError(pid)
        return pid, 0


class _ManagedPopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None


def _pid1_tree(root: Path) -> None:
    _fake_proc(root, 1, ppid=0, pgid=1, sid=1, comm="python3")
    _fake_proc(root, 10, state="Z", sid=50)  # adopted orphan, foreign session
    _fake_proc(root, 11, state="Z", sid=1, pgid=1)  # same session, unregistered
    _fake_proc(root, 12, state="Z", sid=1, pgid=1)  # the managed executor Popen
    _fake_proc(root, 13, state="S", sid=50)  # live orphan: nothing to reap yet


def test_scan_reaps_foreign_session_zombies_and_shields_managed(tmp_path: Path) -> None:
    _pid1_tree(tmp_path)
    _children_file(tmp_path, 1, [10, 11, 12, 13])
    managed = ManagedChildren()
    executor = managed.spawn(lambda: _ManagedPopen(12))  # type: ignore[arg-type,return-value]
    wait = _FakeWait()
    reaper = ZombieReaper(managed, proc_root=tmp_path, self_pid=1, waitpid=wait)

    assert reaper.scan_once() == 1
    assert wait.calls == [10]  # foreign session reaped at once; 11 gets one interval

    assert reaper.scan_once() == 2
    assert wait.calls == [10, 10, 11]  # 11 lingered a whole interval → nobody waits it
    assert 12 not in wait.calls  # registered Popen: its exit status stays with Popen.wait

    executor.returncode = 0  # owner waited it: the pid is free and no longer shielded
    reaper.scan_once()
    reaper.scan_once()  # same-session, so it too gets the one-interval grace first
    assert 12 in wait.calls


def test_scan_falls_back_to_full_proc_scan_without_children_file(tmp_path: Path) -> None:
    _pid1_tree(tmp_path)
    _fake_proc(tmp_path, 20, state="Z", ppid=99, sid=50)  # someone else's zombie
    wait = _FakeWait(raising=frozenset({10}))
    reaper = ZombieReaper(ManagedChildren(), proc_root=tmp_path, self_pid=1, waitpid=wait)

    assert reaper.scan_once() == 0  # ECHILD tolerated (already reaped elsewhere)
    assert wait.calls == [10]


def test_reaping_enabled_only_for_pid1_with_proc(tmp_path: Path) -> None:
    assert reaping_enabled(1, tmp_path)
    assert not reaping_enabled(1, tmp_path / "missing")
    assert not reaping_enabled(4242, tmp_path)


@linux_proc
def test_real_fork_orphan_reaped_while_managed_popen_keeps_exit_status() -> None:
    """真实子进程：另起会话的已退出子进程被收割；登记的 Popen 退出码不被抢收。"""
    managed = ManagedChildren()
    executor = managed.spawn(
        lambda: subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    )
    orphan = os.fork()
    if orphan == 0:  # pragma: no cover - child
        os.setsid()  # agent/code sandbox groups always run in their own session
        os._exit(3)
    reaper = ZombieReaper(managed)
    _wait_until_zombie(orphan)
    _wait_until_zombie(executor.pid)

    reaper.scan_once()
    reaper.scan_once()  # second pass: even same-session lingering zombies are eligible

    with pytest.raises(ChildProcessError):
        os.waitpid(orphan, os.WNOHANG)  # already collected by the reaper
    assert executor.wait(timeout=5) == 7  # not a fabricated 0


@linux_proc
def test_real_same_session_zombie_gets_one_interval_grace() -> None:
    child = os.fork()
    if child == 0:  # pragma: no cover - child
        os._exit(0)
    reaper = ZombieReaper(ManagedChildren())
    _wait_until_zombie(child)

    reaper.scan_once()
    assert procfs.read_stat(child) is not None  # first sighting: left to its owner

    reaper.scan_once()
    assert procfs.read_stat(child) is None  # nobody waited it for an interval: reaped


def _wait_until_zombie(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while (stat := procfs.read_stat(pid)) is None or stat.state != "Z":
        assert time.monotonic() < deadline, f"{pid} never became a zombie"
        time.sleep(0.02)


class _FakePopen:
    pid = 4321
    returncode = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def poll(self) -> None:
        return None


def test_supervisor_registers_executor_and_runs_reaper_only_as_pid1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    token = tmp_path / "register-token"
    token.write_text("secret", encoding="utf-8")
    store.write(
        validate_config(
            {
                "host_url": "http://host.test:8000/",
                "worker_id": "worker-1",
                "max_concurrency": 1,
                "register_token_file": str(token),
            }
        )
    )
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(WorkerSupervisor, "_reap_orphans", lambda self: None)
    monkeypatch.setattr(WorkerSupervisor, "_collect_logs", lambda self, *a: None)

    assert WorkerSupervisor(store, tmp_path / "worker.py")._zombie_reaper is None  # not PID 1

    monkeypatch.setattr(supervisor_module, "reaping_enabled", lambda: True)
    supervisor = WorkerSupervisor(store, tmp_path / "worker.py")
    supervisor._start()

    with supervisor.managed_children.lock:
        assert supervisor.managed_children.pids_locked() == {4321}
    assert supervisor._zombie_reaper is not None


# --- codex P1 R2 on #895：收割路径互斥 + waitpid 紧前现证身份 -----------------


@pytest.mark.parametrize(
    ("state", "start"),
    [("S", 100), ("Z", 999)],  # recycled into a live probe / into another zombie
)
def test_scan_skips_wait_when_pid_recycled_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, start: int
) -> None:
    """扫描读完 stat 后该僵尸被另一路收走、pid 被复用：不得对新进程 waitpid。"""
    _fake_proc(tmp_path, 1, ppid=0, pgid=1, sid=1, comm="python3")
    _fake_proc(tmp_path, 10, state="Z", sid=50, start=100)
    _children_file(tmp_path, 1, [10])
    real_read = procfs.read_stat
    reads = {"n": 0}

    def _read_then_recycle(pid: int, root: Path = procfs.PROC_ROOT) -> procfs.ProcStat | None:
        if pid == 10:
            reads["n"] += 1
            if reads["n"] == 2:  # between the judging read and the pre-wait re-read
                (root / "10" / "stat").write_text(
                    _stat_line(10, "velites", state, 1, 1, 1, start), encoding="utf-8"
                )
        return real_read(pid, root)

    monkeypatch.setattr(procfs, "read_stat", _read_then_recycle)
    wait = _FakeWait()
    reaper = ZombieReaper(ManagedChildren(), proc_root=tmp_path, self_pid=1, waitpid=wait)

    assert reaper.scan_once() == 0
    assert wait.calls == []


def test_scan_skips_wait_when_pid_recycled_into_managed_popen(tmp_path: Path) -> None:
    _fake_proc(tmp_path, 1, ppid=0, pgid=1, sid=1, comm="python3")
    _fake_proc(tmp_path, 10, state="Z", sid=50)
    _children_file(tmp_path, 1, [10])
    managed = ManagedChildren()
    managed.spawn(lambda: _ManagedPopen(10))  # type: ignore[arg-type,return-value]
    wait = _FakeWait()

    ZombieReaper(managed, proc_root=tmp_path, self_pid=1, waitpid=wait).scan_once()

    assert wait.calls == []


def test_collect_group_and_scan_are_mutually_exclusive(tmp_path: Path) -> None:
    """两条收割路径共用 REAP_LOCK：任一持锁时另一路不 wait。"""
    _fake_proc(tmp_path, 1, ppid=0, pgid=1, sid=1, comm="python3")
    _children_file(tmp_path, 1, [])
    reaper = ZombieReaper(ManagedChildren(), proc_root=tmp_path, self_pid=1, waitpid=_FakeWait())
    with REAP_LOCK:
        threads = [
            threading.Thread(target=collect_group, args=(999_999_999, 0.0)),
            threading.Thread(target=reaper.scan_once),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(0.2)
            assert thread.is_alive()  # blocked on the shared lock
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
