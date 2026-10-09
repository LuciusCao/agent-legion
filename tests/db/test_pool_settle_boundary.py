"""Test-boundary pool close must not strand dirty returns (#1045).

Schema rebuilds and session teardown close every pool (per-test isolation
itself only settles, keeping pools alive across tests — see
tests/postgres_support.settle_database_pools). With the #438 ``reset``
callback, psycopg_pool rolls back a dirty (INTRANS) return on a
maintenance worker; ``ConnectionPool.close()`` discards any such task that
has not run yet, so the returned connection keeps its transaction (and
locks) open server-side until GC finalizes it. The next test's TRUNCATE then
waited out the 30s lock_timeout — the load-dependent "setup error" of
test_rejected_claim_event_emits_after_commit and friends.

The scenario is made deterministic by parking every pool worker on a gate
before the dirty return, so the ReturnConnection task is guaranteed to
still be queued when the boundary close starts. The test keeps a reference
to the connection, so a discarded rollback would also stay leaked
deterministically (no GC timing involved).
"""

from __future__ import annotations

import threading

import psycopg
from psycopg.pq import TransactionStatus

from server.app.db.connection import connect_database
from server.app.db.pools import pool_for
from tests.postgres_support import (
    BASE_DATABASE_URL,
    TEST_DATABASE_URL,
    TEST_SCHEMA,
    close_database_pools_settled,
)


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


def test_settled_close_rolls_back_dirty_return_queued_behind_busy_workers() -> None:
    pool = pool_for(TEST_DATABASE_URL)
    gate = threading.Event()
    for _ in range(pool.num_workers):
        pool.run_task(_GatedTask(gate))

    conn = connect_database(TEST_DATABASE_URL)
    raw = conn._raw
    try:
        conn.execute("select count(*) from workspaces").fetchone()
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        conn.close()  # dirty return: rollback queued behind the parked workers
        assert raw.info.transaction_status == TransactionStatus.INTRANS
        assert _foreign_locks_on("workspaces") != []

        # Release the workers only after the boundary close has started: an
        # unsettled close would already have flagged the pool closed, and the
        # queued rollback would be discarded.
        threading.Timer(0.3, gate.set).start()
        close_database_pools_settled()

        # Rolled back (then closed with the pool) — never left INTRANS.
        assert raw.info.transaction_status != TransactionStatus.INTRANS
        assert _foreign_locks_on("workspaces") == []
    finally:
        gate.set()
        raw.close()
