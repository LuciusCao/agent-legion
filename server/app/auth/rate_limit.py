from __future__ import annotations

import threading
import time


class LoginLockedError(Exception):
    """Raised when a username is temporarily locked after repeated failures."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"Too many failed attempts; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class LoginRateLimiter:
    """In-process per-username lockout after consecutive login failures."""

    def __init__(self, max_failures: int = 5, lock_seconds: float = 900.0):
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, username: str) -> None:
        """Raise LoginLockedError while the username is inside its lock window."""
        key = username.strip().lower()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            failures, locked_until = entry
            now = time.monotonic()
            if locked_until > now:
                raise LoginLockedError(int(locked_until - now) + 1)
            if locked_until and locked_until <= now:
                # Lock expired; start a fresh failure window.
                self._entries.pop(key, None)

    def record_failure(self, username: str) -> None:
        key = username.strip().lower()
        with self._lock:
            failures, _ = self._entries.get(key, (0, 0.0))
            failures += 1
            locked_until = (
                time.monotonic() + self._lock_seconds if failures >= self._max_failures else 0.0
            )
            self._entries[key] = (failures, locked_until)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._entries.pop(username.strip().lower(), None)
