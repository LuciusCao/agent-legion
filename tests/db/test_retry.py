from __future__ import annotations

import pytest
from psycopg.errors import DeadlockDetected, SerializationFailure

from server.app.db.retry import is_retryable_database_error, retry_on_database_conflict


def test_succeeds_after_transient_transaction_errors() -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise SerializationFailure("serialization failure")
        return "ok"

    result = retry_on_database_conflict(operation, sleep=delays.append)

    assert result == "ok"
    assert calls["count"] == 3
    assert delays == [0.05, 0.1]


def test_raises_after_exhausting_attempts() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise SerializationFailure("serialization failure")

    with pytest.raises(SerializationFailure, match="serialization failure"):
        retry_on_database_conflict(operation, sleep=lambda _: None)

    assert calls["count"] == 5


def test_does_not_retry_non_database_error() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise RuntimeError("disk I/O error")

    with pytest.raises(RuntimeError, match="disk I/O error"):
        retry_on_database_conflict(operation, sleep=lambda _: None)

    assert calls["count"] == 1


def test_does_not_retry_non_operational_error() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        retry_on_database_conflict(operation, sleep=lambda _: None)

    assert calls["count"] == 1


def test_backoff_delays_double_up_to_cap() -> None:
    delays: list[float] = []

    def operation() -> None:
        raise SerializationFailure("serialization failure")

    with pytest.raises(SerializationFailure, match="serialization failure"):
        retry_on_database_conflict(operation, attempts=8, sleep=delays.append)

    assert delays == [0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0]


def test_retryable_database_error_types() -> None:
    assert is_retryable_database_error(SerializationFailure("serialization failure"))
    assert is_retryable_database_error(DeadlockDetected("deadlock"))
    assert not is_retryable_database_error(RuntimeError("not a database conflict"))
