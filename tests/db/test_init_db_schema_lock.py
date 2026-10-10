"""init_db 迁移 advisory 锁键钉住「库 + 生效 schema」粒度（perf-schema-lock）。

原锁键只含 ``current_database()``：8 个 xdist worker 共用同一测试库、各自
独立 schema（``agent_legion_test_gw*``），会话开局 8 份全量 DDL 在同一
把库级 advisory 锁上串行（每份 ~2.5-3s），尾部 worker 的锁等待超 30s
lock_timeout → LockNotAvailable → 半组测试 setup error。锁键细化到
schema 后同库不同 schema 的 init_db 并行。

矩阵（同步全走 pg_locks 观测 / 线程完成信号，非裸 sleep）：

1. 同 schema 串行保持：A 持 schema 限定键的会话锁，B 在同一 schema 跑
   init_db（init_db_full_check 强制走全路径）必须阻塞在该锁上——锁没了
   或键退化成不含 schema 都会让同步点超时变红。
2. 跨 schema 并行：A 持**旧库级键**（回归探针：原 bug 中 8 个 worker 抢
   的那把锁），B 对另一 scratch schema 跑完整 init_db，必须在 A 仍持锁
   期间完成——B 的键若退化为库级即阻塞，5s lock_timeout 兜底炸红。

xdist 兼容：scratch schema 名按 TEST_SCHEMA 派生（每 worker 唯一）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import psycopg
import pytest
from psycopg import sql

from server.app.db.schema import init_db
from server.app.db.schema_head_cache import init_db_full_check
from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

pytestmark = pytest.mark.postgres

_separator = "&" if "?" in BASE_DATABASE_URL else "?"
# lock_timeout=5s：协议正确时锁等待 = 对方持锁窗口；回归（键退化）时有界炸红
# 而非挂死整个测试会话（比照 tests/db/test_upgrade_lock_domains.py 的纪律）。
_TIMED_SUFFIX = " -clock_timeout=5s"

_SCRATCH_SCHEMA = f"{TEST_SCHEMA}_lockprobe"


def _timed_dsn(schema: str) -> str:
    options = quote(f"-csearch_path={schema}{_TIMED_SUFFIX}", safe="")
    return f"{BASE_DATABASE_URL}{_separator}options={options}"


def _schema_lock_key_sql(schema: str) -> str:
    """新键：与 schema.py 锁点同构（库 + 生效 schema）。"""
    return f"hashtext('agent-legion-schema-' || current_database() || '-' || '{schema}')"


def _database_lock_key_sql() -> str:
    """旧键（回归探针）：原实现只按库加锁，xdist worker 全军抢它。"""
    return "hashtext('agent-legion-schema-' || current_database())"


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """B 侧线程：结果/异常都收进 outcome（冲突也是数据）。"""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=60)
    # B 挂死超过 join 上限必须炸响——否则它的 outcome 为空会被读成假绿。
    assert not thread.is_alive(), "B-side init_db never resolved"


def _await_advisory_waiter(key_sql: str, timeout: float = 10.0) -> None:
    """确定性同步点：等到有 backend 正等待给定表达式的 advisory 锁。

    单键 advisory 锁（objsubid=1）在 pg_locks 里拆成 (classid, objid) 两段
    32 位，按无符号 64 位还原后与目标键比对（比照
    test_upgrade_lock_domains 的 _await_advisory_waiter 手法）。
    """
    with psycopg.connect(_timed_dsn(TEST_SCHEMA), autocommit=True) as probe:
        row = probe.execute(f"select {key_sql}").fetchone()
        assert row is not None
        expected = int(row[0]) & 0xFFFFFFFFFFFFFFFF
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = probe.execute(
                "select classid, objid from pg_locks"
                " where locktype='advisory' and objsubid=1 and not granted"
            ).fetchall()
            for classid, objid in rows:
                if ((int(classid) << 32) | int(objid)) & 0xFFFFFFFFFFFFFFFF == expected:
                    return
            time.sleep(0.02)
    raise AssertionError(f"no backend waited on advisory lock {key_sql!r} within {timeout}s")


def _init_db_full_path(dsn: str) -> None:
    # 会话 fixture 已在本进程验证过该 DSN（head memo 会短路掉锁），强制走
    # 全路径才能保证 B 一定取到 advisory 锁。
    with init_db_full_check():
        init_db(dsn)


def test_init_db_serializes_within_same_schema() -> None:
    """钉住「同 schema 的 init_db 仍被同一把锁串行」：A 持 schema 限定键，
    B 的 init_db 必须等到 A 释放后完成（锁被摘掉或键不含 schema 时同步点
    超时变红——突变自检）。
    """
    key_sql = _schema_lock_key_sql(TEST_SCHEMA)
    conn_a = psycopg.connect(_timed_dsn(TEST_SCHEMA), autocommit=True)
    try:
        conn_a.execute(f"select pg_advisory_lock({key_sql})")
        thread, outcome = _start(lambda: _init_db_full_path(_timed_dsn(TEST_SCHEMA)))
        _await_advisory_waiter(key_sql)
    finally:
        conn_a.close()  # 关闭即释放会话级 advisory 锁
    _join(thread)
    assert outcome.get("error") is None


def test_init_db_parallel_across_schemas() -> None:
    """钉住「同库不同 schema 的 init_db 不再共享锁」：A 持旧库级键（原 bug
    中 8 个 xdist worker 抢的那把锁），B 对 scratch schema 跑完整 init_db
    （全量 DDL + 迁移），必须在 A 仍持锁期间无错完成——键退化回库级时 B
    阻塞、5s lock_timeout 炸红。
    """
    with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(_SCRATCH_SCHEMA))
        )
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(_SCRATCH_SCHEMA)))
    conn_a = psycopg.connect(_timed_dsn(TEST_SCHEMA), autocommit=True)
    try:
        conn_a.execute(f"select pg_advisory_lock({_database_lock_key_sql()})")
        # A 持锁期间 B 必须独立完成；init_db 结束即不变量达成（等完成信号，
        # 非等时长）。
        thread, outcome = _start(lambda: init_db(_timed_dsn(_SCRATCH_SCHEMA)))
        _join(thread)
        assert outcome.get("error") is None
    finally:
        conn_a.close()
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(_SCRATCH_SCHEMA))
            )
