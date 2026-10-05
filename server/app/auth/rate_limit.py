from __future__ import annotations

import threading
import time

# #970：登录失败按两把钥匙计数，任一把进入锁定窗口即拒绝（429）。
# - (账号, 来源 IP)：阈值与旧的按用户名锁定一致（5）——同一来源对同一账号
#   的猜测，锁住的只是这条来源，真实用户从别的来源登录不受影响；
# - 账号（任意来源）：分布式猜测同一账号，阈值更高（20）。它必须高于
#   (账号, IP) 的阈值才有意义：单一来源在窗口内最多贡献 5 次（之后被
#   (账号, IP) 锁拦在验密之前），要锁住账号本身需要多个来源合力。
# 刻意不设「仅按 IP」的钥匙：受支持的部署形态（Docker 端口映射、宿主机
# 反向代理）下全部连接可能共享同一个对端地址，按 IP 计数会让任何人用
# 任意用户名把整个实例锁死。同理，对端地址共享时 (账号, IP) 退化为按
# 账号计数，行为与旧实现相同，不会更差。
# 失败计数只在 failure_window 内累积，过窗自动重开。表达到 _MAX_ENTRIES
# 时先剔除过窗条目，仍满则按窗口起点从旧到新淘汰「未锁定」条目——锁定
# 条目从不淘汰（否则灌入大量新用户名即可冲掉自己的锁定），它们在
# lock_seconds 后自然过期。客户端 IP 由调用方给出（路由层取
# request.client 的对端地址），本模块不解析转发头。
_PAIR, _ACCOUNT = "pair", "account"
_MAX_ENTRIES = 10_000


class LoginLockedError(Exception):
    """Raised when a login key is temporarily locked after repeated failures."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"Too many failed attempts; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class LoginRateLimiter:
    """In-process login lockout on (account, IP) and account keys (#970)."""

    def __init__(
        self,
        max_failures: int = 5,
        lock_seconds: float = 900.0,
        *,
        max_account_failures: int = 20,
        failure_window: float = 900.0,
    ):
        self._limits = {_PAIR: max_failures, _ACCOUNT: max_account_failures}
        self._lock_seconds, self._window = lock_seconds, failure_window
        # key -> (failures, window_started_at, locked_until)
        self._entries: dict[tuple[str, ...], tuple[int, float, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(username: str, client_ip: str | None) -> list[tuple[str, ...]]:
        account = username.strip().lower()
        return [(_PAIR, account, client_ip or ""), (_ACCOUNT, account)]

    def _live(self, key: tuple[str, ...], now: float) -> tuple[int, float, float] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        failures, started, locked_until = entry
        if locked_until > now or (not locked_until and now - started < self._window):
            return entry
        # Lock expired or the failure window lapsed: start fresh.
        self._entries.pop(key, None)
        return None

    def _make_room(self, now: float) -> None:
        for key in list(self._entries):
            self._live(key, now)
        # (window start, key) of every unlocked entry, oldest first.
        unlocked = sorted((e[1], k) for k, e in self._entries.items() if e[2] <= now)
        for _, key in unlocked[: max(0, len(self._entries) - _MAX_ENTRIES + 2)]:
            del self._entries[key]

    def check(self, username: str, client_ip: str | None = None) -> None:
        """Raise LoginLockedError while any key is inside its lock window."""
        now = time.monotonic()
        with self._lock:
            entries = [self._live(key, now) for key in self._keys(username, client_ip)]
        waits = [entry[2] - now for entry in entries if entry is not None and entry[2] > now]
        if waits:
            raise LoginLockedError(int(max(waits)) + 1)

    def record_failure(self, username: str, client_ip: str | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= _MAX_ENTRIES:
                self._make_room(now)
            for key in self._keys(username, client_ip):
                failures, started, _ = self._live(key, now) or (0, now, 0.0)
                failures += 1
                locked = now + self._lock_seconds if failures >= self._limits[key[0]] else 0.0
                self._entries[key] = (failures, started, locked)

    def record_success(self, username: str, client_ip: str | None = None) -> None:
        with self._lock:
            for key in self._keys(username, client_ip):
                self._entries.pop(key, None)
