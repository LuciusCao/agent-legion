"""Unified PostgreSQL connection and transaction contexts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.db.dialect import ConnectSource


@contextmanager
def write_transaction(database_dsn: ConnectSource) -> Iterator[DatabaseConnection]:
    """Yield one PostgreSQL transaction and deterministically release it."""
    conn = connect_database(database_dsn)
    try:
        conn.execute("begin")
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
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        conn.close()


@contextmanager
def read_connection(database_dsn: ConnectSource) -> Iterator[DatabaseConnection]:
    """Yield a pooled PostgreSQL connection for bounded read operations."""
    conn = connect_database(database_dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
