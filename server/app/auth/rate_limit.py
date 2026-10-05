from __future__ import annotations

import threading
import time

# #970：登录失败按三把钥匙计数，任一把进入锁定窗口即拒绝（429）。
# - (账号, IP)：同一来源对同一账号的猜测，阈值最低——攻击者只能锁住
#   自己这条来源，真实用户从别处登录不受影响；
# - 账号（任意 IP）：分布式猜测同一账号，阈值更高；
# - IP（任意账号）：单一来源的密码喷洒，阈值更高。
# 失败计数只在 failure_window 内累积，过窗自动重开；表大小有上限，超限
# 时（_MAX_ENTRIES）先剔除过窗条目。客户端 IP 由调用方给出（路由层取 request.client，
# 即 uvicorn 按 forwarded_allow_ips 处理后的对端地址），本模块不解析头。
_PAIR, _ACCOUNT, _IP = "pair", "account", "ip"
_MAX_ENTRIES = 10_000


class LoginLockedError(Exception):
    """Raised when a login key is temporarily locked after repeated failures."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"Too many failed attempts; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class LoginRateLimiter:
    """In-process login lockout on (account, IP), account and IP keys (#970)."""

    def __init__(
        self,
        max_failures: int = 5,
        lock_seconds: float = 900.0,
        *,
        max_account_failures: int = 20,
        max_ip_failures: int = 20,
        failure_window: float = 900.0,
    ):
        self._limits = {_PAIR: max_failures, _ACCOUNT: max_account_failures, _IP: max_ip_failures}
        self._lock_seconds = lock_seconds
        self._window = failure_window
        # key -> (failures, window_started_at, locked_until)
        self._entries: dict[tuple[str, ...], tuple[int, float, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(username: str, client_ip: str | None) -> list[tuple[str, ...]]:
        account = username.strip().lower()
        keys: list[tuple[str, ...]] = [(_PAIR, account, client_ip or ""), (_ACCOUNT, account)]
        return keys + ([(_IP, client_ip)] if client_ip else [])

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
                for key in list(self._entries):
                    self._live(key, now)
            for key in self._keys(username, client_ip):
                failures, started, _ = self._live(key, now) or (0, now, 0.0)
                failures += 1
                locked = now + self._lock_seconds if failures >= self._limits[key[0]] else 0.0
                self._entries[key] = (failures, started, locked)

    def record_success(self, username: str, client_ip: str | None = None) -> None:
        # The IP key is NOT cleared: a sprayer holding one valid account must
        # not reset its own per-IP counter by logging in between guesses.
        with self._lock:
            for key in self._keys(username, client_ip):
                if key[0] != _IP:
                    self._entries.pop(key, None)
