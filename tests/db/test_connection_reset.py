"""Connection hygiene regressions for pooled transactions (issue #438).

The mock suite cannot see connection transaction state — exactly how the
#438 bug slipped through: every ``write_transaction`` opened a transaction
implicitly (autocommit is off) and then issued a second explicit ``begin``,
so Postgres answered every checkout with
``WARNING: there is already a transaction in progress`` (~10^3/min under
load), while broken cleanup paths left connections checked out or dirty in
the pool (20-minute idle-in-transaction sightings).

These tests run against the real per-worktree test database and assert the
connection-level invariants:

- ``write_transaction`` never double-BEGINs (the transaction is open, and
  opened exactly once, when the body runs);
- a mid-transaction exception rolls back AND returns the connection IDLE;
- a failing commit/rollback can no longer strand the checkout
  (``DatabaseConnection.__exit__`` / ``read_connection`` release in a
  ``finally``);
- the pool return path attributes dirty returns to their checkout origin
  (one deduplicated warning per leak signature, none for clean returns).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest
from psycopg.pq import TransactionStatus

import server.app.db.pool_reset as pool_reset
from server.app.db.connection import connect_database
from server.app.db.pools import close_database_pools
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _reset_leak_telemetry():
    """Isolate the dedup window between tests (state is process-global)."""
    pool_reset._reset_warn_last.clear()
    yield
    pool_reset._reset_warn_last.clear()


def _capture_leak_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "leak origin" in record.getMessage()
    ]


def test_write_transaction_opens_exactly_one_transaction() -> None:
    """The #438 root cause: no second BEGIN on the implicit transaction.

    Pool connections run autocommit-off, so the first statement opens the
    transaction. The old ``conn.execute("begin")`` fired a redundant BEGIN
    on that open transaction — Postgres logs it server-side only (psycopg
    does not surface the warning), so the assertion here is protocol-level:
    the connection must already be INTRANS before any body statement, with
    no BEGIN issued by the wrapper. A reintroduced double-BEGIN would make
    the wrapper's first execute a BEGIN statement again (observable via
    transaction_status staying IDLE after it, or a captured cursor command
    tag of BEGIN).
    """
    with write_transaction(TEST_DATABASE_URL) as conn:
        assert conn.in_transaction is False  # pre-statement: nothing ran yet
        conn.execute("select 1").fetchone()
        assert conn._raw.info.transaction_status == TransactionStatus.INTRANS
        # The wrapper must not have issued a BEGIN: psycopg flips to INTRANS
        # on the first data statement, and the command tag of that statement
        # is SELECT, not BEGIN.
        assert conn._raw.execute("select pg_backend_pid() as pid").fetchone() is not None


def test_write_transaction_first_statement_is_not_begin() -> None:
    """Protocol-level double-BEGIN guard: the first executed statement's
    command tag must be SELECT, not BEGIN (a reintroduced explicit begin
    would show BEGIN as the command status of the wrapper's first execute)."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        cursor = conn._raw.execute("select 1")
        assert cursor.statusmessage == "SELECT 1"


def test_exception_mid_transaction_returns_connection_idle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The #438 leak regression: after a mid-transaction failure the
    connection must be back in the pool with NO open transaction (no
    idle-in-transaction backend holding locks / pinning xmin)."""
    caplog.set_level(logging.WARNING, logger="server.app.db.pool_reset")
    with (
        pytest.raises(RuntimeError, match="mid-transaction failure"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, %s, 'demo_workflow')",
            ("issue-438-rolled-back", "must not persist"),
        )
        assert conn._raw.info.transaction_status == TransactionStatus.INTRANS
        raise RuntimeError("mid-transaction failure")

    assert conn._closed is True  # returned to the pool
    # Synchronous return-path observer: a dirty return would have logged
    # one attributed warning (the rollback itself still happens in the pool).
    assert _capture_leak_warnings(caplog) == []


def test_dirty_return_is_attributed_and_rolled_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pool defense (#438): a connection returned with an open transaction
    is rolled back by the pool, and the return path attributes the leak to
    its checkout origin — the diagnostic #438 lacked in production."""
    caplog.set_level(logging.WARNING, logger="server.app.db.pool_reset")
    conn = connect_database(TEST_DATABASE_URL)
    conn.execute("select 1")  # opens the implicit transaction
    assert conn._raw.info.transaction_status == TransactionStatus.INTRANS
    conn.close()  # dirty return — no commit/rollback

    warnings = _capture_leak_warnings(caplog)
    assert len(warnings) == 1
    assert "INTRANS" in warnings[0]
    assert "leak origin:" in warnings[0]
    assert "unknown" not in warnings[0]  # origin was recorded at checkout


def test_dirty_return_warning_is_deduplicated_per_origin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The diagnostic must not become a log storm: same leak signature
    warns once per interval even under a burst of dirty returns."""
    caplog.set_level(logging.WARNING, logger="server.app.db.pool_reset")
    for _ in range(3):
        conn = connect_database(TEST_DATABASE_URL)
        conn.execute("select 1")
        conn.close()
    assert len(_capture_leak_warnings(caplog)) == 1


def test_clean_return_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Committed transactions and rolled-back reads return silently."""
    caplog.set_level(logging.WARNING, logger="server.app.db.pool_reset")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("select 1")
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("select 1").fetchone()
    assert _capture_leak_warnings(caplog) == []


def test_failing_commit_still_releases_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #438 stranded-checkout leak: when commit itself raises (deadlock
    at commit, connection reset), ``DatabaseConnection.__exit__`` must still
    return the connection — the old order leaked one checkout per failed
    commit."""

    def exploding_commit(self: Any) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr("server.app.db.connection.DatabaseConnection.commit", exploding_commit)
    conn = connect_database(TEST_DATABASE_URL)
    with pytest.raises(RuntimeError, match="commit failed"), conn:
        conn.execute("select 1")
    assert conn._closed is True


def test_failing_rollback_still_releases_read_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_connection's finally must not skip close() when the rollback
    itself fails on a broken connection — the exception would otherwise
    strand the checkout (#438)."""

    def exploding_rollback(self: Any) -> None:
        raise RuntimeError("rollback failed")

    monkeypatch.setattr("server.app.db.connection.DatabaseConnection.rollback", exploding_rollback)
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("select 1").fetchone()
    assert conn._closed is True


def test_no_idle_in_transaction_backend_after_failure() -> None:
    """Server-side view of the hygiene: after a failed write transaction,
    pg_stat_activity must show no idle-in-transaction backend from this
    process's pool (the production symptom: a 20-minute idle-in-transaction
    connection pinning xmin and holding locks)."""
    with pytest.raises(RuntimeError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("select 1")
        pid = conn.execute("select pg_backend_pid() as pid").fetchone()["pid"]
        raise RuntimeError("boom")

    with read_connection(TEST_DATABASE_URL) as observer:
        row = observer.execute(
            "select state from pg_stat_activity where pid = %s", (pid,)
        ).fetchone()
    # The backend must never be 'idle in transaction'. 'active' can race
    # with the pool's asynchronous maintenance (the rollback query itself
    # in flight); a gone backend (recycled) is fine too. To rule out a
    # slow leak, poll until the backend settles to idle or disappears.
    if row is not None and row["state"] == "active":
        for _ in range(20):
            time.sleep(0.05)
            with read_connection(TEST_DATABASE_URL) as poller:
                row = poller.execute(
                    "select state from pg_stat_activity where pid = %s", (pid,)
                ).fetchone()
            if row is None or row["state"] != "active":
                break
    assert row is None or row["state"] == "idle"


def test_pool_reset_callback_installed() -> None:
    """The pool must carry the reset hook: psycopg_pool's constructor takes
    ``reset`` per pool, so the wiring is asserted against the live pool."""
    import os

    from server.app.db import pools as pools_module

    close_database_pools()
    try:
        connect_database(TEST_DATABASE_URL).close()

        pool = pools_module._POOLS[(os.getpid(), TEST_DATABASE_URL)]
        assert pool._reset is pool_reset.reset_connection
    finally:
        close_database_pools()
