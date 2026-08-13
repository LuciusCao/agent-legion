"""Agent-process lifecycle helpers: graceful termination and exit waiting."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time

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


def wait_for_exit(
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
    cancelled: threading.Event | None = None,
) -> tuple[int, bool]:
    """Poll the child, reacting to shutdown/ownership loss/Host cancel.

    Returns (exit_code, report). ``cancelled`` is the batch-2 code path's
    Host-driven cancel (heartbeat body): SIGTERM the process group like a
    shutdown, but report the run as cancelled instead of discarding it."""
    deadline = time.monotonic() + timeout
    while True:
        if ownership_lost.is_set():
            terminate(proc, 5)
            return 1, False
        if cancelled is not None and cancelled.is_set():
            terminate(proc, 5)
            return 130, True
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
