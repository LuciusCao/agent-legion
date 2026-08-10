"""Agent-process lifecycle helpers: graceful termination and exit waiting."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

# Executor 把 agent 子进程的 pid（= pgid，start_new_session=True）记到执行目录；
# executor 被 SIGKILL 来不及清理时，supervisor 按记录 killpg 兜底。
AGENT_PGID_FILENAME = "agent_pgid"


def terminate(proc: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Best-effort process-group SIGTERM then SIGKILL; never raises."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        try:
            proc.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
    print(f"Agent process {proc.pid} did not exit after SIGKILL", flush=True)


def reap_orphaned_agents(work_root: Path, log=print) -> None:
    # SIGTERM→短等待→SIGKILL 清理记录残留的 agent 进程组（ESRCH/EPERM 忽略）。
    for record in work_root.glob(f"*/{AGENT_PGID_FILENAME}"):
        with contextlib.suppress(OSError, ValueError):
            pgid = int(record.read_text(encoding="utf-8"))
            if pgid <= 1:  # 半截/恶意记录：0/-1 会把信号发给本进程组，按垃圾跳过
                continue
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(1)
                os.killpg(pgid, signal.SIGKILL)
            record.unlink(missing_ok=True)
            log(f"reaped orphaned agent process group {pgid}")


def wait_for_exit(
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
) -> tuple[int, bool]:
    """Poll the child, reacting to shutdown/ownership loss. Returns (exit_code, report)."""
    deadline = time.monotonic() + timeout
    while True:
        if ownership_lost.is_set():
            terminate(proc, 5)
            return 1, False
        if shutdown.is_set():
            terminate(proc, shutdown_grace)
            return 130, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate(proc, 5)
            return 124, True
        try:
            return proc.wait(timeout=min(0.5, remaining)), True
        except subprocess.TimeoutExpired:
            continue
