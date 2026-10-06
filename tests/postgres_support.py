from __future__ import annotations

import contextlib
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote


def _worker_schema() -> str:
    worker = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("PYTEST_XDIST_WORKER", "main"))
    return f"agent_legion_test_{worker}"


def _worktree_database_name() -> str:
    # Isolate the test database per worktree: every worktree gets its own
    # database derived from its directory name, so concurrent test runs from
    # different worktrees can no longer drop each other's schemas.
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", Path(__file__).resolve().parents[1].name).lower()
    return f"agent_legion_test_{slug}"


# Tests must never fall back to the ambient AGENT_LEGION_DATABASE_URL: that var
# points at the dev/prod database in real shells, and agent loops exporting it
# have already wiped dev-schema state by running the suite against it. Only an
# explicit AGENT_LEGION_TEST_DATABASE_URL may redirect the test database.
BASE_DATABASE_URL = os.environ.get(
    "AGENT_LEGION_TEST_DATABASE_URL",
    f"postgresql://127.0.0.1:5432/{_worktree_database_name()}",
)
TEST_SCHEMA = _worker_schema()
separator = "&" if "?" in BASE_DATABASE_URL else "?"
TEST_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{separator}options={quote(f'-csearch_path={TEST_SCHEMA}', safe='')}"
)


def ensure_test_database() -> None:
    """Create the per-worktree test database on first use.

    New worktrees get a dedicated database name (see `_worktree_database_name`),
    which would otherwise require a manual `createdb` before the first test run.
    Every xdist worker owns a session fixture, so first use can be concurrent.
    A session-level advisory lock serializes the catalog check and CREATE;
    closing the maintenance connection releases the lock automatically.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(BASE_DATABASE_URL)
    dbname = params.pop("dbname", None)
    if not dbname:
        return
    with psycopg.connect(make_conninfo(**params, dbname="postgres"), autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (dbname,))
        exists = conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
        # Role-isolation guard (scripts/drop-worktree-db.sh): align the
        # derived database owner with agent_legion_dev when the role exists,
        # so the cleanup path can run as a non-superuser role. Best-effort:
        # a creator without ownership-transfer rights simply skips.
        try:
            role = conn.execute(
                "select 1 from pg_roles where rolname = 'agent_legion_dev'"
            ).fetchone()
            if role is not None:
                conn.execute(
                    sql.SQL("alter database {} owner to agent_legion_dev").format(
                        sql.Identifier(dbname)
                    )
                )
        except Exception:
            pass


_POOL_SETTLE_TIMEOUT_SECONDS = 5.0


class _PoolWorkerCheckpoint:
    """Pool maintenance task that parks one pool worker on a shared barrier.

    psycopg_pool workers only ever call ``task.run()`` on dequeued tasks, so
    a duck-typed object is enough. A broken barrier (main-thread timeout)
    must not escape: the worker loop only swallows psycopg client errors and
    any other exception would kill the worker thread.
    """

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def run(self) -> None:
        with contextlib.suppress(threading.BrokenBarrierError):
            self._barrier.wait(_POOL_SETTLE_TIMEOUT_SECONDS)


def _settle_pool_returns(pool) -> None:
    """Block until every maintenance task queued before this call has run.

    #1045: with a ``reset`` callback (server/app/db/pools.py, #438) the
    pool hands every returned connection to a maintenance worker
    (``ReturnConnection``), which rolls back a dirty (INTRANS) return
    asynchronously. ``ConnectionPool.close()`` marks the pool closed first,
    and a task that runs after that is silently discarded — the dirty
    connection is neither rolled back nor closed, its server session keeps
    the open transaction's locks until Python's cyclic GC finalizes the
    connection object, and the next test's TRUNCATE hits the 30s
    lock_timeout. Whether the worker wins the race against the per-test
    close depends on CPU/GIL scheduling, hence the load-dependent flake.

    One checkpoint per worker on a ``num_workers + 1`` barrier: the queue is
    FIFO and a parked worker cannot take a second checkpoint, so the barrier
    trips only after all workers finished everything queued before it. On
    timeout (a wedged worker) the barrier breaks and the caller proceeds —
    the TRUNCATE lock_timeout diagnostic still catches a lingering lock.
    """
    if pool.closed:
        return
    barrier = threading.Barrier(pool.num_workers + 1)
    for _ in range(pool.num_workers):
        pool.run_task(_PoolWorkerCheckpoint(barrier))
    with contextlib.suppress(threading.BrokenBarrierError):
        barrier.wait(_POOL_SETTLE_TIMEOUT_SECONDS)


def close_database_pools_settled() -> None:
    """``close_database_pools`` for test boundaries: settle returns first.

    Pending dirty-return rollbacks finish before the pools close, so no
    returned connection can outlive its test with an open transaction
    (see ``_settle_pool_returns``).
    """
    from server.app.db import pools

    # Pools are keyed by (pid, dsn); a pool inherited across fork has no
    # worker threads in this process and would only stall on the barrier.
    with pools._POOLS_LOCK:
        live = [pool for (pid, _dsn), pool in pools._POOLS.items() if pid == os.getpid()]
    for pool in live:
        _settle_pool_returns(pool)
    pools.close_database_pools()


# Importing test helpers must remain side-effect free. The root PostgreSQL
# session fixture creates the per-worktree database only when a test is marked
# ``postgres``; pure collection and unit runs must work with PostgreSQL offline.
