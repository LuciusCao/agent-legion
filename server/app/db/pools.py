from __future__ import annotations

import os
from threading import Lock
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from server.app.db.dialect import DatabaseDsn
from server.app.db.rows import configure_connection, string_dict_row

_POOLS: dict[tuple[int, str], ConnectionPool[Connection[dict[str, Any]]]] = {}
_POOLS_LOCK = Lock()

_POOL_MAX_SIZE_ENV = "AGENT_LEGION_DB_POOL_MAX_SIZE"
_POOL_MAX_IDLE_ENV = "AGENT_LEGION_DB_POOL_MAX_IDLE"
_POOL_MAX_LIFETIME_ENV = "AGENT_LEGION_DB_POOL_MAX_LIFETIME"
_DEFAULT_POOL_MAX_SIZE = 32
# Recycling defaults (120s idle / 900s lifetime) are tighter than
# psycopg-pool's built-in 600s/3600s: idle shrink closes at most one
# connection per max_idle interval, and a backend's session memory
# (plan/sort caches) only returns to the OS when the connection is recycled,
# so long-lived connections balloon under sustained load.


def _pool_max_size() -> int:
    try:
        return max(1, int(os.environ.get(_POOL_MAX_SIZE_ENV, "")))
    except ValueError:
        return _DEFAULT_POOL_MAX_SIZE


def _pool_seconds(env: str, default: float) -> float:
    try:
        return max(1.0, float(os.environ.get(env, "")))
    except ValueError:
        return default


def pool_for(dsn: DatabaseDsn) -> ConnectionPool[Connection[dict[str, Any]]]:
    key = (os.getpid(), dsn)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ConnectionPool[Connection[dict[str, Any]]](
                conninfo=dsn,
                min_size=1,
                max_size=_pool_max_size(),
                max_idle=_pool_seconds(_POOL_MAX_IDLE_ENV, 120.0),
                max_lifetime=_pool_seconds(_POOL_MAX_LIFETIME_ENV, 900.0),
                timeout=10,
                open=True,
                kwargs={"row_factory": string_dict_row},
                configure=configure_connection,
            )
            _POOLS[key] = pool
        return pool


def close_database_pools() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()
