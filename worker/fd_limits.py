"""Raise the process file-descriptor soft limit at Worker startup.

macOS defaults to a soft RLIMIT_NOFILE of 256, which dozens of concurrent
Pi executions (events file + subprocess pipes + sockets each) plus the
upload threads blow straight through; the hard limit is effectively
unlimited there, so raising the soft limit needs no privileges.
"""

from __future__ import annotations

import resource

MIN_NOFILE = 10240


def raise_fd_limit(min_nofile: int = MIN_NOFILE) -> tuple[int, int]:
    """Raise the RLIMIT_NOFILE soft limit to at least ``min_nofile``.

    Returns the resulting ``(soft, hard)`` pair. Never lowers an existing
    higher soft limit; clamps the target to the hard limit.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min_nofile if hard == resource.RLIM_INFINITY else min(min_nofile, hard)
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        soft = target
    return soft, hard


def raise_fd_limit_startup() -> None:
    """Startup wiring: raise the limit, print the result, never fatal.

    #471 预算腾挪：从 executor.main() 移入（单调用点）。失败仅打一行日志
    继续用默认值——fd 上限是吞吐护栏，不是启动前置条件。"""
    try:
        soft, hard = raise_fd_limit()
        print(f"worker fd limit: soft={soft} hard={hard}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"worker fd limit raise failed; continuing with defaults: {exc}", flush=True)
