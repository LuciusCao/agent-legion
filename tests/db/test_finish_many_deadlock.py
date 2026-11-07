"""跨 workspace 碰撞别名的 finish_many 锁序回归（#1378、#1449）。

从 test_execution_generation_races.py 按被测主题拆出。两批 finish 在
「W1 的 K1 与 W2 的 K1 是同一把锁」的四元组上并发；同步走 pg_locks
观测而非裸 sleep，finish 连接使用 deadlock_timeout=50ms 与
lock_timeout=5s，线程 join 有界。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from server.app.db.transaction import write_transaction
from server.app.executors import _lease_finish_batch as batch_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs.job_id_encoding import encode_job_id
from tests.helpers.agent_worker_api import insert_job_rows


def _code_claim_request(
    workspace_id: str, job_id: str, node_key: str, *, generation: int
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=CODE_EXECUTOR_ID,
        global_capacity=4,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key=workspace_id,
        node_key=node_key,
        capability="review_keywords",
        local_node_limit=None,
        lease_ttl_seconds=60,
        log_path=f"logs/{job_id}-{node_key}.log",
        execution_generation=generation,
    )


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
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
    assert not thread.is_alive(), "B-side transaction never resolved"


def _advisory_holders_for_job(job_db, job_id: str, *, granted: bool) -> list[int]:
    """持有（granted）/等待该 job 的 job-mutation 锁的 backend pid。"""
    with job_db._connect_read() as probe:
        row = probe.execute("select hashtext(%s)", (f"job-mutation:{job_id}",)).fetchone()
        assert row is not None
        expected = int(row["hashtext"]) & 0xFFFFFFFFFFFFFFFF
        rows = probe.execute(
            "select classid, objid, pid from pg_locks"
            " where locktype='advisory' and objsubid=1 and granted=%s",
            (granted,),
        ).fetchall()
    return [
        int(row["pid"])
        for row in rows
        if ((int(row["classid"]) << 32) | int(row["objid"])) & 0xFFFFFFFFFFFFFFFF == expected
    ]


def _await_job_mutation_waiter(job_db, job_id: str, timeout: float = 10.0) -> None:
    """等到有 backend 正等待该 job 的 job-mutation advisory 锁。"""
    with job_db._connect_read() as probe:
        row = probe.execute("select hashtext(%s)", (f"job-mutation:{job_id}",)).fetchone()
        assert row is not None
        expected = int(row["hashtext"]) & 0xFFFFFFFFFFFFFFFF
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = probe.execute(
                "select classid, objid from pg_locks"
                " where locktype='advisory' and objsubid=1 and not granted"
            ).fetchall()
            for row in rows:
                if (
                    (int(row["classid"]) << 32) | int(row["objid"])
                ) & 0xFFFFFFFFFFFFFFFF == expected:
                    return
            time.sleep(0.02)
    raise AssertionError(f"no backend waited on job-mutation:{job_id} within {timeout}s")


def _fetchone(job_db, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with job_db._connect_read() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return dict(row)


def _cross_ws_alias_quadruple(job_db) -> tuple[str, str, str, str]:
    """返回 (job_a, job_b, job_c, job_d)，满足 key(a)==key(d)（K1）、
    key(c)==key(b)（K2）、K1 < K2，且 a/c 在 ws-1、d/b 在 ws-2。"""
    ws1, ws2 = "alias10-ws-1", "alias10-ws-2"
    count = 200_000
    ids1 = [encode_job_id(ws1, "questions", f"cand-{i}") for i in range(count)]
    ids2 = [encode_job_id(ws2, "questions", f"cand-{i}") for i in range(count)]
    with job_db.connect() as conn:
        rows = conn.execute(
            "select t.id as id, hashtext('job-mutation:' || t.id)::int as k"
            " from unnest(%s::text[]) as t(id)",
            (ids1 + ids2,),
        ).fetchall()
    key_by_id = {str(row["id"]): int(row["k"]) for row in rows}
    keys1 = {key_by_id[job_id]: job_id for job_id in ids1}
    keys2 = {key_by_id[job_id]: job_id for job_id in ids2}
    shared = sorted(set(keys1) & set(keys2))
    assert len(shared) >= 2, "200k ids per workspace must share >=2 lock keys"
    k1, k2 = shared[0], shared[1]
    return keys1[k1], keys2[k2], keys1[k2], keys2[k1]


def test_finish_many_cross_ws_alias_quadruple_no_ab_ba(job_db, monkeypatch) -> None:
    """#1378 codex P2 的活回归：跨 ws 碰撞别名四元组上两个 finish_many 批不再
    按相反顺序等同两把锁。

    编舞（等信号非等时长）：A = finish_many([a, b])——monkeypatch 的
    finish_lease 在完成第一项（a）后泊住；#1449 的集中预取允许 A 此刻已持
    K1、K2。B = finish_many([c, d])。纯键序下 B 的第一项是 d（K1）→ 原地
    等待且不持 K2；ws 分桶序（旧代码）下 B 的第一项是 c（W1 桶在前）→ 先拿
    K2 再等 K1。键级断言是「granted K2 holder 若存在则全部属于 A」——既允许
    #1449 的合法预取，也保留「等待中的 B 绝不持 K2」这一突变抓点。双方随后
    干净提交、无 40P01。"""

    @contextlib.contextmanager
    def timed_transaction(source):
        with write_transaction(source) as conn:
            conn.execute("set local deadlock_timeout='50ms'")
            conn.execute("set local lock_timeout='5s'")
            yield conn

    monkeypatch.setattr(batch_module, "write_transaction", timed_transaction)
    job_a, job_b, job_c, job_d = _cross_ws_alias_quadruple(job_db)
    ws_by_job = {
        job_a: "alias10-ws-1",
        job_c: "alias10-ws-1",
        job_d: "alias10-ws-2",
        job_b: "alias10-ws-2",
    }
    repo = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    lease_by_job: dict[str, str] = {}
    for job_id, workspace_id in ws_by_job.items():
        insert_job_rows(
            job_db,
            job_id=job_id,
            node_key="n1",
            limit=20,
            workspace_id=workspace_id,
            agent_id="generator-v1",
        )
        claim = repo.try_claim(_code_claim_request(workspace_id, job_id, "n1", generation=0))
        assert claim is not None
        lease_by_job[job_id] = claim.lease_id

    first_item_done = threading.Event()
    release_a = threading.Event()
    real_finish_lease = batch_module.finish_lease
    calls = {"n": 0}
    a_backend_pids: list[int] = []

    def _blocking_finish_lease(conn, lease_id, result, data_dir=None):  # noqa: ANN001
        calls["n"] += 1
        verdict = real_finish_lease(conn, lease_id, result, data_dir)
        if calls["n"] == 1:
            a_backend_pids.append(int(conn._raw.info.backend_pid))
            # A 已完成第一项并泊住；#1449 允许它已集中预持 K1、K2。
            first_item_done.set()
            assert release_a.wait(timeout=15)
        return verdict

    monkeypatch.setattr(batch_module, "finish_lease", _blocking_finish_lease)
    result = ExecutionResult(status="completed", exit_code=0)
    thread_a, outcome_a = _start(
        lambda: batch_module.finish_many(
            repo, [(lease_by_job[job_a], result, None), (lease_by_job[job_b], result, None)]
        )
    )
    thread_b: threading.Thread | None = None
    outcome_b: dict[str, Any] = {}
    try:
        assert first_item_done.wait(timeout=10), "A never finished its first item"
        thread_b, outcome_b = _start(
            lambda: batch_module.finish_many(
                repo, [(lease_by_job[job_c], result, None), (lease_by_job[job_d], result, None)]
            )
        )
        _await_job_mutation_waiter(job_db, job_d)  # B 卡在 K1（d 的锁）上
        # 关键断言：A 可以合法预持 K2，但等待中的 B 绝不持有 K2。pre-#1449
        # 的空 holder 集同样合法；ws 分桶序让 B 先拿 K2 时此处必红。
        assert a_backend_pids
        k2_holders = _advisory_holders_for_job(job_db, job_b, granted=True)
        assert all(pid == a_backend_pids[0] for pid in k2_holders)
        release_a.set()
        _join(thread_b)
        assert outcome_b.get("error") is None, outcome_b.get("error")
    finally:
        release_a.set()
        _join(thread_a)
        if thread_b is not None and thread_b.is_alive():
            thread_b.join(timeout=30)
    assert outcome_a.get("error") is None, outcome_a.get("error")
    verdicts_a, _ = outcome_a["result"]
    verdicts_b, _ = outcome_b["result"]
    assert verdicts_a == [True, True] and verdicts_b == [True, True]
    for job_id in ws_by_job:
        lease = _fetchone(
            job_db, "select status from executor_leases where id=%s", (lease_by_job[job_id],)
        )
        assert lease["status"] == "released"
