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

    适用边界：单端判定（存在任意被 holder 阻塞者）只对随 xdist schema 隔离的
    锁安全（行锁——他 worker 会话碰不到本 schema 的行，无法与 holder 形成阻塞
    边）。advisory 全域键是全实例共享的，同键他 worker 的等待者也会被本 holder
    阻塞、让单端探针在目标 waiter 到达锁点前提前返回——那种场景必须用
    ``wait_until_waiter_blocked_by`` 的双端形态（#1211 / PR #1224 codex P2）。
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


def wait_until_waiter_blocked_by(
    waiter_pid: int,
    holder_pid: int,
    *,
    thread: threading.Thread | None = None,
    finished_ok: bool = False,
    timeout: float = 10.0,
    dsn: str = TEST_DATABASE_URL,
) -> None:
    """双端钉死：轮询直到 ``waiter_pid`` 的 pg_blocking_pids 包含 ``holder_pid``。

    与 ``wait_until_blocked_by`` 的分工见后者的 docstring——advisory 全域键
    （全实例共享、不被 xdist schema 隔离）必须双端：同键的无关等待者也被
    holder 阻塞，只有「目标 waiter 的阻塞列表含 holder」才证明目标已真实
    到达锁点。``waiter_pid`` 由被观测线程经连接 ``info.backend_pid`` 回传
    （握手先例 tests/workflows/test_sharding_concurrency.py）。pg_blocking_pids
    按 pid 直查，无需扫 pg_stat_activity。``thread`` / ``finished_ok`` 语义与
    单端版对齐。
    """
    deadline = time.monotonic() + timeout
    with psycopg.connect(dsn, autocommit=True) as monitor:
        while True:
            row = monitor.execute(
                "select %s = any(pg_blocking_pids(%s))",
                (holder_pid, waiter_pid),
            ).fetchone()
            if row is not None and row[0]:
                return
            if thread is not None and not thread.is_alive():
                if finished_ok:
                    return
                raise AssertionError(
                    f"thread finished without being blocked by backend {holder_pid}"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"backend {waiter_pid} not blocked by {holder_pid} within {timeout}s"
                )
            time.sleep(0.005)
