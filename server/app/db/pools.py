from __future__ import annotations

import os
from threading import Lock
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from server.app.db.dialect import DatabaseDsn
from server.app.db.pool_reset import reset_connection
from server.app.db.pool_settle import settle_pool_returns
from server.app.db.pool_sizing import pool_max_idle, pool_max_lifetime, pool_max_size
from server.app.db.rows import configure_connection, string_dict_row

_POOLS: dict[tuple[int, str], ConnectionPool[Connection[dict[str, Any]]]] = {}
_POOLS_LOCK = Lock()


def pool_for(dsn: DatabaseDsn) -> ConnectionPool[Connection[dict[str, Any]]]:
    key = (os.getpid(), dsn)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ConnectionPool[Connection[dict[str, Any]]](
                conninfo=dsn,
                min_size=1,
                max_size=pool_max_size(),
                max_idle=pool_max_idle(),
                max_lifetime=pool_max_lifetime(),
                timeout=10,
                open=True,
                kwargs={"row_factory": string_dict_row},
                configure=configure_connection,
                # #438: defensive rollback on return. psycopg_pool already
                # rolls back INTRANS returns; this reset hook additionally
                # attributes the leak to its checkout call site (see
                # pool_reset.reset_connection) and verifies IDLE afterwards.
                reset=reset_connection,
            )
            _POOLS[key] = pool
        return pool


def settle_database_pools() -> None:
    """Run every queued return of this process's pools, keeping them open.

    The per-test isolation barrier (#1045): pools stay alive for the whole
    test worker session (per-test close/rebuild is costly under xdist
    contention), and the only synchronization TRUNCATE needs beforehand is
    that pending dirty-return rollbacks have run (see pool_settle). Idle
    pooled connections hold no transaction and no locks (the #438 reset
    hook verifies IDLE on return), so they never block the TRUNCATE.
    Pools are keyed by (pid, dsn); a pool inherited across fork has no
    worker threads in this process and would only stall on the barrier, so
    it is skipped.
    """
    with _POOLS_LOCK:
        live = [pool for (pid, _dsn), pool in _POOLS.items() if pid == os.getpid()]
    for pool in live:
        settle_pool_returns(pool)


def close_database_pools() -> None:
    """Close every pool, settling this process's queued returns first.

    The single pool-close chokepoint (#1077): production lifespan shutdown
    and test boundaries both go through it, so a dirty return queued just
    before shutdown is rolled back (and the connection closed with the
    pool) instead of being discarded by ``ConnectionPool.close()``.
    """
    with _POOLS_LOCK:
        entries = list(_POOLS.items())
        _POOLS.clear()
    for (pid, _dsn), pool in entries:
        if pid == os.getpid():
            settle_pool_returns(pool)
        pool.close()
