from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool

from server.app.db.cursor import Cursor
from server.app.db.dialect import ConnectSource, postgres_sql, resolve_dsn
from server.app.db.dialect import DatabaseDsn as DatabaseDsn  # noqa: F401 (re-export)
from server.app.db.pool_reset import note_return, record_checkout_origin
from server.app.db.pools import close_database_pools as close_database_pools
from server.app.db.pools import pool_for

_Params = Sequence[Any] | Mapping[str, Any] | None


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

    def execute(self, sql: str, params: _Params = None) -> Cursor:
        return Cursor(self._raw.execute(postgres_sql(sql), params))

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> Cursor:
        cursor = self._raw.cursor()
        cursor.executemany(postgres_sql(sql), params_seq)
        return Cursor(cursor)

    def stream(self, name: str, sql: str, params: _Params = None, *, chunk_size: int) -> Cursor:
        """Server-side cursor streaming rows in chunk_size batches; name unique per transaction."""
        cursor = self._raw.cursor(name=name)
        cursor.itersize = chunk_size
        cursor.execute(postgres_sql(sql), params)
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
        # #438: observe BEFORE the pool's async reset — this is the only
        # synchronous point where a dirty return can be attributed to its
        # checkout site. The pool rolls the transaction back either way.
        note_return(self._raw)
        self._pool.putconn(self._raw)

    def __enter__(self) -> DatabaseConnection:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        # close() is guaranteed (#438): when commit/rollback itself raises
        # (deadlock at commit, connection reset), the old order skipped
        # close() entirely — one stranded checkout per failed commit, and a
        # pool slot lost while the connection may still hold its
        # transaction open server-side. The pool's reset hook cleans
        # whatever state the connection is left in; a commit failure still
        # propagates to the caller after the checkout is released.
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


def connect_database(dsn: ConnectSource) -> DatabaseConnection:
    """Pooled connection; ``dsn`` also accepts the JobQueries facade (#187)."""
    dsn = resolve_dsn(dsn)
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("AGENT_LEGION_DATABASE_URL must be a PostgreSQL URL")
    # A failed checkout must not tear down any pool: ConnectionPool recovers
    # on its own, and other threads may still hold healthy connections.
    pool = pool_for(dsn)
    raw = pool.getconn()
    # Leak telemetry for the pool reset hook (#438): remember the checkout
    # site so a dirty return can be attributed. Bounded frame walk, no
    # formatting until a leak actually hits the reset hook.
    record_checkout_origin(raw, sys._getframe())
    return DatabaseConnection(raw, pool, dsn)
