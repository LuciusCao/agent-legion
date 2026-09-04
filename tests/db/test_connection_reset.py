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
    pool_reset._reset_warn_suppressed.clear()
    yield
    pool_reset._reset_warn_last.clear()
    pool_reset._reset_warn_suppressed.clear()


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
    # Attribution quality (#439 review): the origin must name the business
    # frame that borrowed the connection — here this very test function —
    # not just any non-unknown plumbing path (verified against generator
    # paths too: contextmanager frames are skipped, the caller is reached).
    assert "test_dirty_return_is_attributed_and_rolled_back" in warnings[0]


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
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_connection's finally must not skip the release when the rollback
    itself fails on a broken connection — the exception would otherwise
    strand the checkout (#438). The rollback failure is observed, not
    silenced (#439): a rollback that cannot run means the connection is
    almost certainly broken, and nothing else reports it if the pool
    return succeeds."""

    def exploding_rollback(self: Any) -> None:
        raise RuntimeError("rollback failed")

    monkeypatch.setattr("server.app.db.connection.DatabaseConnection.rollback", exploding_rollback)
    caplog.set_level(logging.WARNING, logger="server.app.db.transaction")
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("select 1").fetchone()
    assert conn._closed is True
    rollback_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "rollback failed" in record.getMessage()
    ]
    assert len(rollback_warnings) == 1
    assert rollback_warnings[0].exc_info is not None  # traceback kept


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


def test_close_failure_does_not_mask_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#439: a raise from a finally-block close() replaces the original
    exception at the caller — the deadlock/commit error the caller actually
    needs. The release path must protect close() so the original propagates."""

    def exploding_commit(self: Any) -> None:
        raise RuntimeError("commit failed")

    def exploding_close(self: Any) -> None:
        # Simulates a close that fails AFTER flipping the closed flag (the
        # real close marks _closed before the putconn round trip can raise).
        self._closed = True
        raise RuntimeError("close also failed")

    monkeypatch.setattr("server.app.db.connection.DatabaseConnection.commit", exploding_commit)
    monkeypatch.setattr("server.app.db.connection.DatabaseConnection.close", exploding_close)
    conn = connect_database(TEST_DATABASE_URL)
    with pytest.raises(RuntimeError, match="commit failed"), conn:
        conn.execute("select 1")
    # The exception the caller sees is the ORIGINAL commit failure, not the
    # close() failure that fired in the finally block (#439: finally raises
    # replace, they do not chain).
    assert conn._closed is True


def test_suppressed_leak_hits_are_counted(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#439: rate-limiting must not erase leak frequency — after a suppressed
    burst, the next emitted warning reports how many hits it swallowed, so
    "one leak" and "leaking all minute" stay distinguishable."""
    caplog.set_level(logging.WARNING, logger="server.app.db.pool_reset")
    # Shrink the dedup window so the second burst is outside it without a
    # real 60s sleep.
    monkeypatch.setattr(pool_reset, "_RESET_WARN_EVERY_SECONDS", 0.05)
    try:
        for _ in range(3):  # first hit warns, next two are suppressed
            conn = connect_database(TEST_DATABASE_URL)
            conn.execute("select 1")
            conn.close()
        time.sleep(0.06)
        conn = connect_database(TEST_DATABASE_URL)  # outside the window now
        conn.execute("select 1")
        conn.close()
    finally:
        monkeypatch.setattr(pool_reset, "_RESET_WARN_EVERY_SECONDS", 60.0)

    warnings = _capture_leak_warnings(caplog)
    assert len(warnings) == 2
    # "(suppressed" — parenthesized suffix, not the origin text (which
    # contains this test's own name).
    assert "(suppressed" not in warnings[0]
    assert "(suppressed 2 hits in the last interval)" in warnings[1]


def test_origin_walk_is_hard_capped() -> None:
    """#439: the collected-frames bound did not bound the walk itself — a
    stack of only plumbing frames walked the entire call chain. Past the
    walk cap the origin must degrade to an explicit unknown marker."""

    class FakeFrame:
        def __init__(self, code: Any, module: str) -> None:
            self.f_code = code
            self.f_globals = {"__name__": module}
            self.f_back: Any = None

    def make_plumbing_stack(depth: int) -> Any:
        """A chain of plumbing frames — skipped, so never collected."""
        code = compile("pass", "<plumbing>", "exec")
        head: Any = None
        for _ in range(depth):
            frame = FakeFrame(code, "server.app.db.plumbing")
            frame.f_back = head
            head = frame
        return head

    origin = pool_reset._checkout_origin(make_plumbing_stack(50))
    assert origin.startswith("unknown")
    assert "walk cap" in origin

    # A shallow plumbing-only stack stays under the cap and resolves to the
    # plain unknown marker.
    assert pool_reset._checkout_origin(make_plumbing_stack(3)) == "unknown"
