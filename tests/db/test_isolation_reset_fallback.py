"""Failure-path contract of the per-test isolation reset.

Pins the guarantees that happy-path runs never exercise:

- a probe statement error (cached table listing naming a dropped table)
  must still run the all-dirty TRUNCATE for real — the probe runs in its
  own transaction segment precisely so its failure cannot poison the
  queued writes (tests/isolation_support.reset_schema_data docstring);
- the poisoned table-list cache must self-heal (refreshed from pg_tables),
  so one outlaw DDL cannot fail every reset for the rest of the session;
- a probe blocked by a leaked lock must reach the #1045 blocker
  attribution (_fail_on_leaked_locks), not a generic "PostgreSQL is
  required" failure.
"""

from __future__ import annotations

import psycopg
import pytest
from _pytest.outcomes import Failed

from tests import isolation_support
from tests.postgres_support import TEST_DATABASE_URL

_PHANTOM = "phantom_dropped_table"


def _insert_artifact_row() -> None:
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("insert into artifacts(hash, size) values ('isolation-fallback', 1)")


def _assert_reset_landed() -> None:
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        assert conn.execute("select count(*) from artifacts").fetchone()[0] == 0
        seed = conn.execute(
            "select value from global_settings where key = 'token_usage'"
        ).fetchone()
        assert seed is not None


def test_probe_statement_error_still_truncates_and_heals_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isolation_support._TABLE_LIST is not None  # fixture reset ran first
    # Same-length swap (users -> phantom): the count guard cannot see it, so
    # the probe statement itself fails on the dropped name.
    poisoned = [_PHANTOM if table == "users" else table for table in isolation_support._TABLE_LIST]
    monkeypatch.setattr(isolation_support, "_TABLE_LIST", poisoned)
    _insert_artifact_row()

    assert isolation_support.reset_schema_data() is True

    _assert_reset_landed()
    # Self-heal: the cache was refreshed from pg_tables, and the next reset
    # probes cleanly instead of cascading statement errors.
    assert _PHANTOM not in isolation_support._TABLE_LIST
    assert "users" in isolation_support._TABLE_LIST
    assert isolation_support.reset_schema_data() is True


def test_probe_count_guard_recovers_from_stale_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isolation_support._TABLE_LIST is not None
    # A duplicated real name makes the cached listing longer than pg_tables:
    # the server-side count guard trips (no statement error involved).
    monkeypatch.setattr(isolation_support, "_TABLE_LIST", [*isolation_support._TABLE_LIST, "users"])
    _insert_artifact_row()

    assert isolation_support.reset_schema_data() is True

    _assert_reset_landed()
    assert isolation_support._TABLE_LIST.count("users") == 1


def test_probe_lock_wait_reaches_blocker_attribution() -> None:
    conn = isolation_support.reset_connection()
    conn.execute("set lock_timeout = '1s'")  # keep the test fast; restored below
    blocker = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    blocker.execute("begin")
    # ACCESS EXCLUSIVE conflicts with the probe's EXISTS (AccessShare), so
    # the dirty probe waits behind the blocker until lock_timeout fires.
    blocker.execute("lock table users in access exclusive mode")
    try:
        with pytest.raises(Failed, match="dirty-table probe"):
            isolation_support.reset_schema_data()
    finally:
        blocker.execute("rollback")
        blocker.close()
        conn.execute("set lock_timeout = '30s'")
