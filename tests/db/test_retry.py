from __future__ import annotations

import sqlite3

import pytest

from server.app.db.retry import is_lock_error, retry_on_sqlite_lock


def test_succeeds_after_transient_lock_errors() -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = retry_on_sqlite_lock(operation, sleep=delays.append)

    assert result == "ok"
    assert calls["count"] == 3
    # The first retry runs immediately; the next one waits the 50ms base delay.
    assert delays == [0.05]


def test_raises_after_exhausting_attempts() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise sqlite3.OperationalError("database is busy")

    with pytest.raises(sqlite3.OperationalError, match="database is busy"):
        retry_on_sqlite_lock(operation, sleep=lambda _: None)

    assert calls["count"] == 4


def test_does_not_retry_non_lock_operational_error() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        retry_on_sqlite_lock(operation, sleep=lambda _: None)

    assert calls["count"] == 1


def test_does_not_retry_non_operational_error() -> None:
    calls = {"count": 0}

    def operation() -> None:
        calls["count"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        retry_on_sqlite_lock(operation, sleep=lambda _: None)

    assert calls["count"] == 1


def test_backoff_delays_double_up_to_cap() -> None:
    delays: list[float] = []

    def operation() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        retry_on_sqlite_lock(operation, attempts=8, sleep=delays.append)

    assert delays == [0.05, 0.1, 0.2, 0.4, 0.8, 1.0]


def test_is_lock_error_messages() -> None:
    assert is_lock_error(sqlite3.OperationalError("database is locked"))
    assert is_lock_error(sqlite3.OperationalError("database table is locked"))
    assert is_lock_error(sqlite3.OperationalError("database is busy"))
    assert not is_lock_error(sqlite3.OperationalError("no such table: jobs"))
