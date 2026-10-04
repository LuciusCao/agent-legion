"""PID 1 zombie reaping for the containerized Worker Service (#682).

In the worker image ``python -m worker.service`` is the container's PID 1.
When the executor dies abnormally (SIGKILL by the OOM killer), its in-flight
sandbox processes are reparented to PID 1 — and the supervisor never wait()ed
them, so every exited orphan stayed a zombie forever.

Why not a blanket ``waitpid(-1, WNOHANG)`` / SIGCHLD handler: the supervisor
process also owns ``subprocess.Popen`` children (the executor, short runtime
probes from HTTP handlers). Reaping one of those behind its Popen's back makes
``Popen.wait`` hit ECHILD and report a fabricated ``returncode == 0``. So the
reaper waits only on *specific* pids it has proven are not someone's Popen:

- the pid is a zombie whose parent is us (``/proc``, no ``ps`` needed);
- it is not a registered managed child (``ManagedChildren`` — the executor
  Popen is registered under the same lock that spawns it);
- and either it lives in another session (the supervisor never spawns with a
  new session; agent/code sandbox groups always run ``start_new_session``, so
  a foreign-session child can only be an adopted orphan), or it has stayed a
  zombie across two scans (an unregistered same-session Popen — e.g. a
  ``subprocess.run`` probe — is waited within milliseconds; one that lingers
  a full interval has nobody waiting on it).

Only active when this process is PID 1 and ``/proc`` exists; elsewhere the
real init (launchd, systemd, tini via ``init: true``) already reaps orphans.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from worker import procfs

REAP_INTERVAL_SECONDS = 5.0
COLLECT_TIMEOUT_SECONDS = 1.0

# Every wait() this module issues — the periodic scan and the orphan reaper's
# ``collect_group`` — runs under this lock (codex P1 on #895): otherwise one
# path can reap a zombie the other already decided on, the pid gets recycled
# (e.g. by a runtime probe ``subprocess.run`` in an HTTP handler) and the late
# ``waitpid(pid)`` steals that probe's exit status.
REAP_LOCK = threading.Lock()


def collect_group(pgid: int, timeout: float = COLLECT_TIMEOUT_SECONDS) -> int:
    """wait() exited members of process group ``pgid`` that are our children.

    Used right after the orphan reaper's SIGKILL so the kill does not just mint
    fresh zombies. ``waitpid(-pgid)`` only matches *our* children inside that
    group — orphans reparented to us as PID 1 — never the supervisor's own
    Popen children, which live in the supervisor's group (callers refuse that
    pgid). ECHILD (nothing of ours there: the normal non-PID-1 case) ends the
    loop at once; members still dying at the deadline are left to the periodic
    ``ZombieReaper`` scan. Returns how many were collected.
    """
    reaped = 0
    deadline = time.monotonic() + timeout
    while True:
        try:
            with REAP_LOCK:
                pid, _status = os.waitpid(-pgid, os.WNOHANG)
        except (ChildProcessError, PermissionError):
            return reaped
        if pid:
            reaped += 1
            continue
        if time.monotonic() >= deadline:
            return reaped
        time.sleep(0.02)


class ManagedChildren:
    """Popen children whose exit status belongs to their owner, not the reaper."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._procs: list[subprocess.Popen[Any]] = []

    def spawn(self, factory: Callable[[], subprocess.Popen[Any]]) -> subprocess.Popen[Any]:
        """Create and register a Popen atomically w.r.t. reaper scans."""
        with self.lock:
            proc = factory()
            self._procs.append(proc)
            return proc

    def forget(self, proc: subprocess.Popen[Any]) -> None:
        """Drop a Popen its owner has waited (codex P2 on #895).

        Without a reaper (non-PID-1: native installs, ``init: true`` containers)
        nothing else would ever prune the table, so every executor restart would
        pin the exited Popen and its stdout pipe forever.
        """
        with self.lock:
            self._procs = [known for known in self._procs if known is not proc]

    def pids_locked(self) -> set[int]:
        # Caller holds ``lock``. A Popen whose returncode is set has been waited
        # already: its pid is free (and may be reused), so it stops being shielded.
        self._procs = [proc for proc in self._procs if proc.returncode is None]
        return {proc.pid for proc in self._procs}


def reaping_enabled(pid: int | None = None, proc_root: Path = procfs.PROC_ROOT) -> bool:
    return (os.getpid() if pid is None else pid) == 1 and proc_root.is_dir()


class ZombieReaper:
    def __init__(
        self,
        managed: ManagedChildren,
        log: Callable[[str], None] = print,
        *,
        proc_root: Path = procfs.PROC_ROOT,
        self_pid: int | None = None,
        waitpid: Callable[[int, int], tuple[int, int]] = os.waitpid,
        interval: float = REAP_INTERVAL_SECONDS,
    ) -> None:
        self.managed = managed
        self._log = log
        self._proc_root = proc_root
        self._self_pid = os.getpid() if self_pid is None else self_pid
        self._waitpid = waitpid
        self._interval = interval
        self._lingering: set[tuple[int, int]] = set()  # (pid, starttime) seen as zombie last scan
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def scan_once(self) -> int:
        """Reap provably-orphaned zombie children once; returns how many."""
        own = procfs.read_stat(self._self_pid, self._proc_root)
        if own is None:
            return 0
        reaped = 0
        lingering: set[tuple[int, int]] = set()
        # The managed lock spans the scan and the waits: the supervisor registers
        # the executor Popen under it, so no Popen can be born unregistered
        # mid-scan. REAP_LOCK serialises against ``collect_group``.
        with self.managed.lock, REAP_LOCK:
            shielded = self.managed.pids_locked()
            for pid in procfs.child_pids(self._self_pid, self._proc_root):
                if pid in shielded:
                    continue
                stat = procfs.read_stat(pid, self._proc_root)
                if stat is None or stat.state != "Z" or stat.ppid != self._self_pid:
                    continue
                key = (pid, stat.starttime)
                if stat.sid == own.sid and key not in self._lingering:
                    lingering.add(key)  # same-session: give its owner one interval
                    continue
                if not self._same_zombie(stat):
                    continue  # reaped and recycled since the read: not ours to wait
                try:
                    done, _status = self._waitpid(pid, os.WNOHANG)
                except (ChildProcessError, PermissionError):
                    continue  # already reaped elsewhere / not ours
                if done:
                    reaped += 1
        self._lingering = lingering
        return reaped

    def _same_zombie(self, seen: procfs.ProcStat) -> bool:
        """Re-read right before ``waitpid``: still the very zombie child we judged."""
        now = procfs.read_stat(seen.pid, self._proc_root)
        return (
            now is not None
            and now.state == "Z"
            and now.ppid == self._self_pid
            and now.starttime == seen.starttime
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="zombie-reaper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                reaped = self.scan_once()
            except OSError as exc:  # /proc vanished or unreadable: keep the thread alive
                self._log(f"PID 1 僵尸收割扫描失败：{exc}")
                continue
            if reaped:
                self._log(f"PID 1 已收割 {reaped} 个孤儿僵尸进程")
