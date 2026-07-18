"""Bounded retry for transient SQLite lock contention.

SQLite runs with a single writer; concurrent writers surface
``sqlite3.OperationalError`` lock errors once ``busy_timeout`` is exhausted.
Callers wrap an idempotent connect-and-transact unit with
:func:`retry_on_sqlite_lock` so a retry gets a fresh connection.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)


def is_lock_error(exc: sqlite3.OperationalError) -> bool:
    """Return True when the error message indicates SQLite lock contention."""
    message = str(exc).lower()
    return any(marker in message for marker in _LOCK_MESSAGES)


def retry_on_sqlite_lock(
    operation: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay_seconds: float = 0.05,
    max_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run *operation*, retrying transient SQLite lock errors with backoff.

    Only :class:`sqlite3.OperationalError` values whose message indicates a
    lock are retried; any other error propagates immediately. The first retry
    runs without sleeping, then the delay doubles from *base_delay_seconds* up
    to *max_delay_seconds*. *operation* must be safe to re-run after rollback.
    """
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc) or attempt + 1 >= attempts:
                raise
            if attempt > 0:
                sleep(min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds))
    # The final loop iteration always returns or raises; this is unreachable.
    raise sqlite3.OperationalError("retry_on_sqlite_lock: attempts exhausted")  # pragma: no cover
