"""Restart-backoff policy constants for the supervisor's crash loop.

Split from ``worker/supervisor.py`` (#250 budget floors) so the supervisor
keeps process orchestration while the backoff curve lives next to its
documentation. ``worker/supervisor.py`` re-binds each constant under its own
module namespace (``_RESTART_BACKOFF_INITIAL`` etc.), so the existing test
anchors that monkeypatch the supervisor module keep steering the live loop.
"""

from __future__ import annotations

_EXIT_REFUSED = 2  # Host 拒绝注册 / Worker 被吊销 / 启动预检失败：不自动重启，进入 failed
_RESTART_BACKOFF_INITIAL = 5.0
_RESTART_BACKOFF_MAX = 300.0
_STABLE_AFTER = 60.0  # 稳定运行超过该时长后重置退避
_STOP_GRACE_MAX = 22.0  # kill 后再等 3s，总预算 25s，低于 compose 的 30s
_KILL_WAIT = 3.0


def restart_delay(restart_count: int) -> float:
    """Exponential backoff for the Nth automatic restart (1-based), capped."""
    return min(_RESTART_BACKOFF_INITIAL * (2 ** (restart_count - 1)), _RESTART_BACKOFF_MAX)
