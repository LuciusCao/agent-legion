"""Unified PostgreSQL connection and transaction contexts."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.db.dialect import ConnectSource

logger = logging.getLogger(__name__)


def _release_quietly(conn: DatabaseConnection) -> None:
    """close() for finally blocks: never mask the caller's original error."""
    # #439: close() can raise (broken connection, pool shutdown); a raise
    # from a finally block replaces the propagating original at the caller —
    # the deadlock/reset error the caller actually needs would be demoted to
    # a __context__ footnote. #204 broad-except audit: must-run release path,
    # failure modes un-narrowable from here; the pool's reset/discard path
    # still reports the connection, this branch only drops a breadcrumb.
    try:
        conn.close()
    except Exception:
        logger.debug("close() failed during release (#439)", exc_info=True)


@contextmanager
def write_transaction(database_dsn: ConnectSource) -> Iterator[DatabaseConnection]:
    """Yield one PostgreSQL transaction and deterministically release it.

    Pool connections are psycopg defaults (autocommit off), so the caller's
    first statement opens the transaction implicitly — no explicit ``begin``
    is issued here. The legacy ``conn.execute("begin")`` fired a second BEGIN
    on an already-open transaction and Postgres answered every checkout with
    ``WARNING: there is already a transaction in progress`` (#438): at high
    concurrency that is a warning per claim/heartbeat/enqueue round trip.
    """
    conn = connect_database(database_dsn)
    try:
        try:
            yield conn
        except Exception:
            # #204 broad-except audit: compensate-then-bare-reraise（#233
            # 模式）。with 块的产出空间是调用方的全部业务体，故必然无法
            # 收窄——本臂的职责只有一件事：在原异常继续传播前对连接执行
            # rollback，保证失败的事务绝不残留半提交状态。裸 ``raise``
            # 原样重抛原始异常，不转换也不掩盖类型；此处不做日志，因为
            # rollback 本身无诊断价值，真正的异常与 traceback 由业务
            # 调用方的处理路径（retry/HTTP 层）负责记录。
            _rollback_quietly(conn)
            raise
        else:
            conn.commit()
    finally:
        _release_quietly(conn)


def _rollback_quietly(conn: DatabaseConnection) -> None:
    """Rollback; a raise here must never skip the release (#438) or the log (#439)."""
    # #204 broad-except audit: the caller's business exception is already
    # propagating (compensate path); rollback failures are a broken
    # connection or pool shutdown — un-narrowable from here, and re-raising
    # would mask the original error and strand the checkout (#438). The
    # WARN keeps the failure observable (#439): the pool reset hook only
    # reports a dirty connection that is successfully RETURNED, so this log
    # is the only witness when the connection died mid-transaction.
    try:
        conn.rollback()
    except Exception:
        logger.warning("rollback failed; connection likely broken (#439)", exc_info=True)


@contextmanager
def read_connection(database_dsn: ConnectSource) -> Iterator[DatabaseConnection]:
    """Yield a pooled PostgreSQL connection for bounded read operations.

    The rollback in ``finally`` returns the connection IDLE even when the
    caller's read opened an implicit transaction; it too is guarded so a
    broken connection cannot skip ``close()`` (#438 — an exception raised
    before ``conn.close()`` in a finally block would strand the checkout).
    """
    conn = connect_database(database_dsn)
    try:
        yield conn
    finally:
        _rollback_quietly(conn)
        _release_quietly(conn)
