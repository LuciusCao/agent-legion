"""PostgreSQL 锁等待的条件同步（#955 测试卫生）。

并发测试常要确认「另一线程的事务已经阻塞在某把行锁 / advisory 锁上」再推进
下一步。固定 ``time.sleep`` 只能猜时序：慢机上线程尚未到达锁点就推进（假阳性
通过或误判），快机上白白多等。这里改为观察 ``pg_blocking_pids``：持锁会话的
backend pid 出现在某个等待者的阻塞列表里，即证明等待已真实发生。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import psycopg

from tests.postgres_support import TEST_DATABASE_URL


def backend_pid(conn: Any) -> int:
    """当前连接的 backend pid（兼容 dict / tuple 行工厂）。"""
    row = conn.execute("select pg_backend_pid() as pid").fetchone()
    return int(row["pid"] if isinstance(row, dict) else row[0])


def wait_until_blocked_by(
    holder_pid: int,
    *,
    thread: threading.Thread | None = None,
    finished_ok: bool = False,
    timeout: float = 10.0,
    dsn: str = TEST_DATABASE_URL,
) -> None:
    """轮询直到有会话被 ``holder_pid`` 阻塞（锁等待已发生）。

    传入 ``thread`` 时，若该线程在阻塞之前就已结束则立即失败——被测语义要求它
    必须卡在锁上，提前跑完说明没有走到锁点。``finished_ok=True`` 用于「对方
    要么阻塞、要么已跑完」都合法的握手（例如修复后不再阻塞的死锁回归）：线程
    结束即返回。
    """
    deadline = time.monotonic() + timeout
    with psycopg.connect(dsn, autocommit=True) as monitor:
        while True:
            row = monitor.execute(
                "select exists(select 1 from pg_stat_activity"
                " where %s = any(pg_blocking_pids(pid)))",
                (holder_pid,),
            ).fetchone()
            if row is not None and row[0]:
                return
            if thread is not None and not thread.is_alive():
                if finished_ok:
                    return
                raise AssertionError(f"thread finished without blocking on backend {holder_pid}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"no session blocked by backend {holder_pid} within {timeout}s")
            time.sleep(0.005)
