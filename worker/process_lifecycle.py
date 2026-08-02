"""Agent-process lifecycle helpers: graceful termination and exit waiting."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time


def terminate(proc: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Best-effort process-group SIGTERM then SIGKILL; never raises."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        print(f"Agent process {proc.pid} did not exit after SIGKILL", flush=True)


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
