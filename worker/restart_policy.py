"""Restart-backoff policy for the supervisor's crash loop.

Split from ``worker/supervisor.py`` (#250 budget floors). The curve reads its
initial/cap constants from **caller-supplied arguments**, not this module's
globals: the supervisor passes its own module-level constants, so the
existing test anchors that monkeypatch ``worker.supervisor`` keep steering
the live loop (the value-rebinding alone did NOT do that — subagent review
on PR #257 caught the dead anchor).
"""

from __future__ import annotations

_EXIT_REFUSED = 2  # Host 拒绝注册 / Worker 被吊销 / 启动预检失败：不自动重启，进入 failed
_RESTART_BACKOFF_INITIAL = 5.0
_RESTART_BACKOFF_MAX = 300.0
_STABLE_AFTER = 60.0  # 稳定运行超过该时长后重置退避
_STOP_GRACE_MAX = 22.0  # kill 后再等 3s，总预算 25s，低于 compose 的 30s
_KILL_WAIT = 3.0


def restart_delay(restart_count: int, *, initial: float, cap: float) -> float:
    """Exponential backoff for the Nth automatic restart (1-based), capped."""
    exponential: float = initial * (2 ** (restart_count - 1))
    return min(exponential, cap)


# #681: a crash auto-restart used to reset ``claim_enabled`` to false like a
# cold start, so one executor SIGKILL (VM OOM) left a healthy, registered
# Worker idling until an operator noticed — the Host had requeued its lost
# leases and nobody claimed them for hours. A crash restart now keeps the
# switch the operator turned on, bounded twice: never inside a crash loop
# (the previous executor did not run ``_STABLE_AFTER``), and at most
# ``CLAIM_RESUME_LIMIT`` resumes per rolling ``CLAIM_RESUME_WINDOW_SECONDS``
# — past either bound the cold-start pause (deliberate design) applies.
# Cold start, console restart and manual start still always reset.
CLAIM_RESUME_LIMIT = 3
CLAIM_RESUME_WINDOW_SECONDS = 3600.0


def claim_resume_verdict(
    claim_enabled: bool, restart_count: int, resumes: list[float], now: float
) -> tuple[bool, str]:
    """(keep claims?, panel log line) for one crash auto-restart.

    ``restart_count`` is the supervisor's 1-based counter for this restart
    (reset to 0 after a stable run, so 1 = the crashed executor had run
    stably); ``resumes`` = monotonic stamps of earlier kept resumes."""
    if not claim_enabled:
        return False, "启动时已将 claim_enabled 重置为 false，需在控制台重新打开认领"
    if restart_count != 1:
        return False, (
            "执行进程短时间内反复崩溃：自动重启已将 claim_enabled 重置为 false，"
            "排查崩溃原因后在控制台重新打开认领"
        )
    recent = [stamp for stamp in resumes if now - stamp < CLAIM_RESUME_WINDOW_SECONDS]
    if len(recent) >= CLAIM_RESUME_LIMIT:
        return False, (
            f"执行进程 1 小时内已崩溃重启 {len(recent)} 次：自动重启已将 claim_enabled"
            " 重置为 false，排查崩溃原因后在控制台重新打开认领"
        )
    return (
        True,
        "执行进程崩溃后自动重启：保留已开启的认领（claim_enabled=true），新进程按 ramp_up 重新爬坡",
    )
