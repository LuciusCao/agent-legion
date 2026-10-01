"""upgrade 重验两个写路径的锁域串行化（#759 P2-A/P2-B，EXEC-GENERATION-001）。

手法比照 tests/db/test_execution_generation_races.py：A 连接在主线程手工控
事务持 advisory 锁（模拟 guard 重验），B 在线程里跑真实写路径；同步点用
pg_locks 观测「B 正等待该 advisory 锁」（非裸 sleep），TIMED_DATABASE_URL
带 deadlock_timeout=50ms + lock_timeout=5s 兜底（意外等待有界），
join(timeout=30) 后断言线程已死防假绿。

矩阵（每案 docstring 标明钉住的锁域规则）：

1. P2-A guard→publish：guard 侧先持 implementation-publication 锁，发布
   事务（create_workflow_revision_with_projection 真实写路径）被阻塞至
   guard 提交，随后正常落地。
2. P2-A publish→guard：发布事务持锁未提交（新 active 已插入未提交），
   guard 模拟（取锁 + 重读 active revision）被阻塞至发布提交，随后读到
   新 revision——在飞发布先于重读时 upgrade 看到新图而非旧图。
3. P2-B guard→relock：guard 侧持 skill-lock 全域锁，put_lock（dispatch
   首次 pin / make skills-lock 的唯一写入口）被阻塞至 guard 提交；提交
   后的 relock 对后续读可见。
4. P2-B 写→读：写侧持 skill-lock 锁且写入未提交（in-flight relock），
   get_lock_locked（plan 阶段读法）被阻塞至写提交并读到新值。

xdist 兼容：同步全走 pg_locks 观测 + threading.Event + join 超时；每案
workspace id 独立，TRUNCATE 隔离照常。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import psycopg

from server.app.db.connection import connect_database
from server.app.jobs import JobQueries
from server.app.jobs.queries.global_settings import (
    SKILL_LOCK_ADVISORY_SCOPE,
    acquire_skill_lock_domain_lock,
)
from server.app.jobs.queries.upgrade_impl_identity import (
    acquire_implementation_publication_lock,
)
from server.app.jobs.queries.workflow_revision_projection import (
    create_workflow_revision_with_projection,
)
from server.app.services.skill_lock_store import SkillLockStore
from server.app.skills.config import SkillsLock
from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

# 与 test_execution_generation_races.py 同款纪律：50ms 让重引入的环在毫秒级
# 现形，5s 给所有意外等待兜底（协议正确时锁等待 = 对方的提交流程）。
_separator = "&" if "?" in BASE_DATABASE_URL else "?"
TIMED_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{_separator}options="
    f"{quote(f'-csearch_path={TEST_SCHEMA} -cdeadlock_timeout=50ms -clock_timeout=5s', safe='')}"
)

_COMMIT_V1 = "1" * 40
_COMMIT_V2 = "2" * 40


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
    thread.join(timeout=30)
    # B 挂死超过 join 上限必须炸响——否则它的 outcome 为空会被读成假绿。
    assert not thread.is_alive(), "B-side transaction never resolved"


def _await_advisory_waiter(scope: str, timeout: float = 10.0) -> None:
    """确定性同步点：等到有 backend 正等待该 scope 的 advisory 锁。

    单键 advisory 锁（objsubid=1）在 pg_locks 里拆成 (classid, objid) 两段
    32 位，按无符号 64 位还原后与 hashtext 比对（比照
    test_execution_generation_races 的 _await_job_mutation_waiter 手法）。
    """
    with psycopg.connect(TIMED_DATABASE_URL, autocommit=True) as probe:
        row = probe.execute("select hashtext(%s)", (scope,)).fetchone()
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
    raise AssertionError(f"no backend waited on advisory lock {scope!r} within {timeout}s")


def _publish_revision(workspace_id: str, version: int, definition_hash: str) -> None:
    """真实发布写路径：新连接一事务（archive 旧 active + insert 新 active）。"""
    conn = connect_database(TIMED_DATABASE_URL)
    try:
        with conn:
            create_workflow_revision_with_projection(
                conn,
                revision_id=f"{workspace_id}:wf:v{version}",
                workspace_id=workspace_id,
                workflow_key="wf",
                version=version,
                status="active",
                definition_json="{}",
                definition_hash=definition_hash,
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. P2-A guard→publish
# ---------------------------------------------------------------------------


def test_revision_publish_blocks_behind_guard_lock(job_db) -> None:
    """钉住「发布在 guard 重验的 publication 锁下串行」（#759 P2-A）：A 持
    implementation-publication 锁（模拟 guard 重读期间），B 的发布事务必须
    阻塞至 A 提交，随后发布正常落地（新 active 可见）。

    突变自检：摘掉 create_workflow_revision_with_projection 的取锁后 B 不再
    等待（同步点超时）——本用例变红。
    """
    workspace = job_db.create_workspace("lockdom1", default_workflow_key="wf")
    workspace_id = str(workspace["id"])
    _publish_revision(workspace_id, 1, "h1")

    conn_a = connect_database(TIMED_DATABASE_URL)
    try:
        acquire_implementation_publication_lock(conn_a, workspace_id)
        thread, outcome = _start(lambda: _publish_revision(workspace_id, 2, "h2"))
        _await_advisory_waiter(f"implementation-publication:{workspace_id}")
        conn_a.commit()
    finally:
        conn_a.close()
    _join(thread)

    assert outcome.get("error") is None
    current = job_db.get_active_workflow_revision(workspace_id, "wf")
    assert current is not None
    assert str(current["id"]) == f"{workspace_id}:wf:v2"


# ---------------------------------------------------------------------------
# 2. P2-A publish→guard
# ---------------------------------------------------------------------------


def test_guard_reread_waits_for_inflight_publish_and_sees_new_revision(job_db) -> None:
    """钉住「在飞发布先于 guard 重读落地时，guard 看到新图」（#759 P2-A）：
    B 的发布事务持锁未提交，A 的 guard 模拟（取锁 + 重读 active revision）
    必须阻塞至 B 提交，随后读到新 revision——upgrade 不会 pin 到刚被取代
    的旧 revision。
    """
    workspace = job_db.create_workspace("lockdom2", default_workflow_key="wf")
    workspace_id = str(workspace["id"])
    _publish_revision(workspace_id, 1, "h1")
    queries_timed = JobQueries(TIMED_DATABASE_URL, job_db.jobs_dir)

    inserted = threading.Event()
    release = threading.Event()

    def _publish_slow() -> None:
        conn_b = connect_database(TIMED_DATABASE_URL)
        try:
            # 隐式事务：取锁 + archive + insert 新 active，未提交。
            create_workflow_revision_with_projection(
                conn_b,
                revision_id=f"{workspace_id}:wf:v2",
                workspace_id=workspace_id,
                workflow_key="wf",
                version=2,
                status="active",
                definition_json="{}",
                definition_hash="h2",
            )
            inserted.set()
            assert release.wait(timeout=10), "main thread never released the publish"
            conn_b.commit()
        finally:
            conn_b.close()

    def _guard_reread() -> str:
        with queries_timed.connect() as conn_a:
            queries_timed.acquire_implementation_publication_lock(conn_a, workspace_id)
            current = queries_timed.get_active_workflow_revision(workspace_id, "wf")
            assert current is not None
            return str(current["id"])

    thread_b, outcome_b = _start(_publish_slow)
    assert inserted.wait(timeout=10), "publish thread never reached the insert"
    thread_a, outcome_a = _start(_guard_reread)
    _await_advisory_waiter(f"implementation-publication:{workspace_id}")
    release.set()
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_b.get("error") is None
    assert outcome_a["result"] == f"{workspace_id}:wf:v2"


# ---------------------------------------------------------------------------
# 3. P2-B guard→relock
# ---------------------------------------------------------------------------


def test_skill_lock_write_blocks_behind_guard_lock(job_db) -> None:
    """钉住「relock 在 guard 重验的 skill-lock 锁下串行」（#759 P2-B）：A 持
    skill-lock 全域锁（模拟 guard 重验期间），B 的 SkillLockStore.put_lock
    必须阻塞至 A 提交；guard 提交后的 relock 对后续读可见（语义正确：
    重验读到的锁文档 ≥ 任何在重验前已完成的 relock）。

    突变自检：摘掉 put_global_settings_document_under_lock 的取锁后 B 不再
    等待（同步点超时）——本用例变红。
    """
    lock = SkillsLock.model_validate({"skills": {"g/s": {"repo": "", "refs": {"v1": _COMMIT_V2}}}})
    conn_a = connect_database(TIMED_DATABASE_URL)
    try:
        acquire_skill_lock_domain_lock(conn_a)
        thread, outcome = _start(lambda: SkillLockStore(TIMED_DATABASE_URL).put_lock(lock))
        _await_advisory_waiter(SKILL_LOCK_ADVISORY_SCOPE)
        conn_a.commit()
    finally:
        conn_a.close()
    _join(thread)

    assert outcome.get("error") is None
    stored = SkillLockStore(TIMED_DATABASE_URL).get_lock()
    assert stored is not None
    assert stored.skills["g/s"].refs["v1"] == _COMMIT_V2


# ---------------------------------------------------------------------------
# 4. P2-B 写→读
# ---------------------------------------------------------------------------


def test_locked_read_waits_for_inflight_write_and_sees_new_lock(job_db) -> None:
    """钉住「plan 阶段锁内读不旧于并发 relock」（#759 P2-B）：B 持
    skill-lock 锁且写入未提交（in-flight relock），A 的 get_lock_locked
    必须阻塞至 B 提交并读到新值——upgrade plan 不会基于将被取代的旧锁
    文档规划继承。

    突变自检：摘掉 get_global_settings_document_under_lock 的取锁后 A 不再
    等待（同步点超时）且读到旧值——两条断言同时变红。
    """
    seed = SkillsLock.model_validate({"skills": {"g/s": {"repo": "", "refs": {"v1": _COMMIT_V1}}}})
    SkillLockStore(TIMED_DATABASE_URL).put_lock(seed)
    new_document = {"skills": {"g/s": {"repo": "", "refs": {"v1": _COMMIT_V2}}}}

    written = threading.Event()
    release = threading.Event()

    def _relock_slow() -> None:
        conn_b = connect_database(TIMED_DATABASE_URL)
        try:
            # 模拟 put_lock 的持锁未提交窗口（in-flight relock）。
            acquire_skill_lock_domain_lock(conn_b)
            conn_b.execute(
                "insert into global_settings(key, value) values ('skill_lock', %s)"
                " on conflict(key) do update set value=excluded.value",
                (json.dumps(new_document),),
            )
            written.set()
            assert release.wait(timeout=10), "main thread never released the relock"
            conn_b.commit()
        finally:
            conn_b.close()

    thread_b, outcome_b = _start(_relock_slow)
    assert written.wait(timeout=10), "relock thread never reached the write"
    thread_a, outcome_a = _start(lambda: SkillLockStore(TIMED_DATABASE_URL).get_lock_locked())
    _await_advisory_waiter(SKILL_LOCK_ADVISORY_SCOPE)
    release.set()
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_b.get("error") is None
    stored = outcome_a["result"]
    assert stored is not None
    assert stored.skills["g/s"].refs["v1"] == _COMMIT_V2
