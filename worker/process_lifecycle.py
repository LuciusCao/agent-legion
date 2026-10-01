"""Agent-process lifecycle helpers: graceful termination and exit waiting.

``wait_for_exit`` moved to the event-driven watcher
(``worker/execution/exit_watch.py``, #647 — #578 phases 2/3: one kernel-event
watcher thread replaces the per-execution 0.5s poll loop). This module keeps
``terminate`` and the pgid record; the poll loop below survives ONLY as the
watcher's fail-closed degradation path.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

# Executor 把 agent 子进程的 pid（= pgid，start_new_session=True）记到执行目录；
# executor 被 SIGKILL 来不及清理时，supervisor 按记录 killpg 兜底。
AGENT_PGID_FILENAME = "agent_pgid"


def terminate(proc: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Best-effort process-group SIGTERM then SIGKILL; never raises.

    #640：killpg 对已退出/换组的进程组会抛 EPERM（PermissionError）或
    ESRCH（ProcessLookupError），同为 OSError 子类。terminate 是收尾路径
    （run_execution 的 finally / shutdown / cancel / 超时收尾都经它），
    信号送不到一律按「进程已不可达」处理：记一行日志后继续 wait 确认，
    绝不向上炸穿执行线程拖垮整个 executor。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except OSError as exc:
            # ESRCH=进程组已消失；EPERM=组已退出/被换（POSIX 允许对这类
            # 进程组返回 EPERM）。两者都意味着本档信号送不到，非致命：
            # 落到 wait 用退出码确认子进程终结（真实退出时 wait 立即返回）。
            print(
                f"killpg {proc.pid} {sig.name} failed ({exc!r}); treating process group as gone",
                flush=True,
            )
        try:
            proc.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        except OSError as exc:
            # 防御性兜底（never-raises 契约的最后一环）：Popen.wait 的现实
            # 异常面只有 TimeoutExpired（ECHILD/EINTR 已被 subprocess 内部
            # 消化），这里兜的是子进程被别处 reap 等边缘竞态下的 waitpid
            # OSError——同样按已消失处理返回，不上抛。
            print(
                f"wait for agent process {proc.pid} failed ({exc!r}); treating it as gone",
                flush=True,
            )
            return
    print(f"Agent process {proc.pid} did not exit after SIGKILL", flush=True)


def poll_wait_locally(
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
    cancelled: threading.Event | None = None,
) -> tuple[int, bool]:
    """Legacy per-execution poll loop (pre-#647 semantics) — the ONLY caller
    is the exit watcher's fail-closed degradation path (watcher thread died).
    Production waits go through ``worker.execution.exit_wait.wait_for_exit``.

    Poll the child, reacting to shutdown/ownership loss/Host cancel.
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
