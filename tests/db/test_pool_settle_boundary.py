"""Pool close must not strand returned connections (#1045, #1077).

``server.app.db.pools.close_database_pools`` is the single close chokepoint:
the production lifespan shutdown and the test boundaries (schema rebuilds,
session teardown) both call it, so these tests cover both (per-test
isolation itself only settles, keeping pools alive across tests — see
``pools.settle_database_pools``). With the #438 ``reset`` callback every
return is a ``ReturnConnection`` task on a pool maintenance worker;
``ConnectionPool.close()`` discards any such task that has not run yet, so
the returned connection is neither returned nor closed until GC finalizes it.

Two layers, one test each:

- the settle barrier: a return queued before the close runs before
  ``pool.close()``, so the connection is closed with the pool;
- the synchronous rollback in ``DatabaseConnection.close`` (#1077 codex P1):
  a dirty return that lands after the barrier (a DB call still running past
  the lifespan's bounded joins) is already rolled back on the caller's
  thread, so a discarded task can never keep a transaction (snapshot,
  locks) alive. Before that, the next test's TRUNCATE waited out the 30s
  lock_timeout — the load-dependent "setup error" of #1045.

Deterministic by parking every pool worker on a gate before the return, so
the ReturnConnection task is guaranteed to still be queued. The gate is
released from *inside* the close path (a wrapped ``settle_pool_returns`` /
``pool.close``), never by a fixed timer (#1336): a timer can fire before the
close even starts when the main thread is paused, draining the queue early
and turning the test into a no-op. The tests keep a reference to the
connection, so a discarded task stays visible (no GC timing involved).
"""

from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from server.app.db import pools
from server.app.db.connection import connect_database
from server.app.db.pools import close_database_pools, pool_for
from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA


class _GatedTask:
    """Duck-typed pool maintenance task that parks a worker on a gate."""

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def run(self) -> None:
        self._gate.wait(5)


def _foreign_locks_on(table: str) -> list[tuple]:
    with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
        return conn.execute(
            """
            select l.pid, l.mode
            from pg_locks l
            join pg_class c on c.oid = l.relation
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = %s and c.relname = %s
              and l.granted and l.pid <> pg_backend_pid()
            """,
            (TEST_SCHEMA, table),
        ).fetchall()


def _park_workers(pool) -> threading.Event:
    gate = threading.Event()
    for _ in range(pool.num_workers):
        pool.run_task(_GatedTask(gate))
    return gate


def test_settled_close_runs_return_queued_behind_busy_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = pool_for(TEST_DATABASE_URL)
    gate = _park_workers(pool)
    conn = connect_database(TEST_DATABASE_URL)
    raw = conn._raw
    try:
        conn.execute("select count(*) from workspaces").fetchone()
        conn.commit()
        conn.close()  # ReturnConnection queued behind the parked workers
        assert raw.closed is False

        # Release the workers only once the close path has entered the settle
        # barrier (#1336 handshake, replacing the fixed 0.3s timer): the queued
        # return is provably still pending, so only the barrier can save it —
        # a broken barrier leaves the task to be discarded by pool.close() and
        # the assertion below goes red. A fixed timer could fire before the
        # close even started (main thread paused after arming it), letting the
        # workers drain the return normally and the test pass vacuously.
        real_settle = pools.settle_pool_returns

        def settle_then_release(target: object) -> None:
            gate.set()
            real_settle(target)

        monkeypatch.setattr(pools, "settle_pool_returns", settle_then_release)
        close_database_pools()

        assert raw.closed is True  # returned, then closed with the pool
    finally:
        gate.set()
        raw.close()


def test_dirty_return_after_the_barrier_never_keeps_its_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Model a return landing after the barrier: the close does not wait for
    # it at all, so the queued ReturnConnection is discarded.
    monkeypatch.setattr(pools, "settle_pool_returns", lambda pool: None)
    pool = pool_for(TEST_DATABASE_URL)
    gate = _park_workers(pool)
    conn = connect_database(TEST_DATABASE_URL)
    raw = conn._raw
    try:
        conn.execute("select count(*) from workspaces").fetchone()
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        assert _foreign_locks_on("workspaces") != []
        conn.close()  # dirty return, workers parked: no pool rollback possible

        # Rolled back on the caller's thread, before any pool task.
        assert raw.info.transaction_status == TransactionStatus.IDLE
        assert _foreign_locks_on("workspaces") == []

        # Handshake instead of a fixed timer (#1336): the gate is released
        # only after pool.close() has returned, so the queued ReturnConnection
        # is provably discarded — the worker can never sneak the return in
        # ahead of the close and turn the test into a no-op.
        real_close = pool.close

        def close_then_release() -> None:
            try:
                real_close()
            finally:
                gate.set()

        monkeypatch.setattr(pool, "close", close_then_release)
        close_database_pools()
        assert raw.info.transaction_status != TransactionStatus.INTRANS
        assert _foreign_locks_on("workspaces") == []
    finally:
        gate.set()
        raw.close()
