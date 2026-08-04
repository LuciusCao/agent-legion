"""Bounded retry for transient PostgreSQL transaction failures."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import TypeVar

from psycopg import Error
from psycopg.errors import DeadlockDetected, SerializationFailure

T = TypeVar("T")


def is_retryable_database_error(exc: BaseException) -> bool:
    return isinstance(exc, (DeadlockDetected, SerializationFailure))


def retry_on_database_conflict(
    operation: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay_seconds: float = 0.05,
    max_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(attempts):
        try:
            return operation()
        except Error as exc:
            if not is_retryable_database_error(exc) or attempt + 1 >= attempts:
                raise
            sleep(min(base_delay_seconds * (2**attempt), max_delay_seconds))
    raise RuntimeError("database retry loop exhausted")


def with_database_conflict_retry(fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> T:
        return retry_on_database_conflict(lambda: fn(*args, **kwargs))

    return wrapper
