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
_DEFAULT_POOL_MAX_SIZE = 32


def _pool_max_size() -> int:
    try:
        return max(1, int(os.environ.get(_POOL_MAX_SIZE_ENV, "")))
    except ValueError:
        return _DEFAULT_POOL_MAX_SIZE


def pool_for(dsn: DatabaseDsn) -> ConnectionPool[Connection[dict[str, Any]]]:
    key = (os.getpid(), dsn)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ConnectionPool[Connection[dict[str, Any]]](
                conninfo=dsn,
                min_size=1,
                max_size=_pool_max_size(),
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
