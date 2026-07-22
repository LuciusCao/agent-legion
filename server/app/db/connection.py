from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from threading import Lock
from typing import Any

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool

from server.app.db.cursor import Cursor
from server.app.db.dialect import DatabaseDsn, postgres_sql
from server.app.db.rows import configure_connection, string_dict_row

_POOLS: dict[tuple[int, str], ConnectionPool[Connection[dict[str, Any]]]] = {}
_POOLS_LOCK = Lock()


class DatabaseConnection:
    """Small DB-API facade used by existing query modules."""

    def __init__(
        self,
        raw: Connection[dict[str, Any]],
        pool: ConnectionPool[Connection[dict[str, Any]]],
        dsn: str,
    ) -> None:
        self._raw = raw
        self._pool = pool
        self.database_dsn = dsn
        self._closed = False

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> Cursor:
        return Cursor(self._raw.execute(postgres_sql(sql), params))

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> Cursor:
        cursor = self._raw.cursor()
        cursor.executemany(postgres_sql(sql), params_seq)
        return Cursor(cursor)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    @property
    def in_transaction(self) -> bool:
        return self._raw.info.transaction_status != TransactionStatus.IDLE

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool.putconn(self._raw)

    def __enter__(self) -> DatabaseConnection:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def _pool_for(dsn: DatabaseDsn) -> ConnectionPool[Connection[dict[str, Any]]]:
    key = (os.getpid(), dsn)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ConnectionPool[Connection[dict[str, Any]]](
                conninfo=dsn,
                min_size=1,
                max_size=32,
                timeout=10,
                open=True,
                kwargs={"row_factory": string_dict_row},
                configure=configure_connection,
            )
            _POOLS[key] = pool
        return pool


def connect_database(dsn: DatabaseDsn) -> DatabaseConnection:
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("VIDEO_HIVE_DATABASE_URL must be a PostgreSQL URL")
    pool = _pool_for(dsn)
    # A failed checkout (pool timeout or unreachable server) must not tear down
    # any pool: ConnectionPool recovers on its own, and other threads may still
    # hold healthy connections checked out from it.
    return DatabaseConnection(pool.getconn(), pool, dsn)


def close_database_pools() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()
