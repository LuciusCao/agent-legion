"""Unified PostgreSQL connection and transaction contexts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.db.dialect import ConnectSource


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
            # #204 broad-except audit: compensate-then-bare-re-raise（#233
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
        conn.close()


def _rollback_quietly(conn: DatabaseConnection) -> None:
    """Rollback, letting a broken connection surface at close()/pool reset.

    If rollback itself raises (connection reset mid-transaction), the
    exception must not skip ``conn.close()`` — that would check the
    connection out forever (#438: the 20-minute idle-in-transaction
    sightings). The pool's reset hook is the second line of defense for
    whatever state the connection is still in.
    """
    # noqa rationale: the rollback failure is deliberately swallowed —
    # close() below is the must-run path, and a broken connection surfaces
    # again at the pool return (reset hook / discard).
    with suppress(Exception):
        conn.rollback()


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
        conn.close()
