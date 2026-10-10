"""Batch claim transaction tests (issue #546; #555 two-phase split).

``claim_batch`` selects candidates on a read-only connection
(``claim_batch_select``) and promotes them in ONE compact write transaction
(``claim_batch_tx``): per-pool caps (agent_limit/code_limit), the mid-batch
capacity accounting (the WorkerView snapshot must be incremented per
promote), the SAVEPOINT containment of ClaimRacedError (first k claims
survive a raced candidate), and the empty/scan-skipped verdicts. The #555
write-phase revalidation / lock-face family lives in the sister file
test_agent_broker_claim_batch_two_phase.py (800-line split discipline). The
single-claim path (``broker.claim``) is covered byte-for-byte by the
pre-#546 suites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from server.app.agent_broker.claim_batch import claim_batch
from server.app.agent_control.registry import AgentWorkerRegistry
from shared.protocol import PROTOCOL_VERSION
from tests.helpers.agent_worker_api import (
    broker,
    enqueue_code,
    insert_code_job_rows,
    seed_request,
)
from tests.postgres_support import TEST_DATABASE_URL


def _register_worker(**overrides: Any) -> None:
    payload: dict[str, Any] = {
        "worker_id": "worker-1",
        "name": "worker",
        "runtimes": ["pi"],
        "max_concurrency": 10,
        "max_code_concurrency": 10,
        "labels": {"arch": "arm64"},
        "capabilities": ["generate"],
        "models": [{"provider": "gateway", "model": "test-model", "runtime": "pi"}],
        # code 池准入要求 protocol v2+（CODE_PROTOCOL_VERSION）。
        "protocol_version": PROTOCOL_VERSION,
    }
    payload.update(overrides)
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(**payload)


def _seed_agent_jobs(job_db, count: int, *, prefix: str = "job") -> None:
    for index in range(count):
        seed_request(job_db, job_id=f"{prefix}-{index}")


def _seed_code_jobs(job_db, count: int, *, prefix: str = "code") -> None:
    pool = broker(job_db.jobs_dir.parent)
    for index in range(count):
        insert_code_job_rows(job_db, job_id=f"{prefix}-{index}")
        enqueue_code(pool, job_id=f"{prefix}-{index}")


def _queued_count(job_db, *, kind: str | None = None) -> int:
    predicate = "and kind=%s" if kind else ""
    params = (kind,) if kind else ()
    with job_db._connect_read() as conn:
        row = conn.execute(
            f"select count(*) as c from agent_execution_requests where state='queued' {predicate}",
            params,
        ).fetchone()
    return int(row["c"])


def test_batch_claim_promotes_up_to_limit_in_one_pass(job_db) -> None:
    _seed_agent_jobs(job_db, 6)
    _register_worker()

    claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=4)

    assert len(claims) == 4
    assert {claim.kind for claim in claims} == {"agent"}
    # 剩余 2 个保持 queued；批内 promote 的 lease/node_run 各自独立。
    assert _queued_count(job_db) == 2
    assert len({claim.lease_id for claim in claims}) == 4
    assert len({claim.node_run_id for claim in claims}) == 4


def test_batch_claim_clamps_to_max_batch_claims(job_db) -> None:
    """Host 侧硬顶（MAX_BATCH_CLAIMS）：请求超过时按上限截断。"""
    # 不真造 257 个 job——直接把上限常量缩小验证 clamp 路径（#555 起常量
    # 随选择段迁到 claim_batch_select）。
    import server.app.agent_broker.claim_batch_select as select_module

    _register_worker()
    original = select_module.MAX_BATCH_CLAIMS
    select_module.MAX_BATCH_CLAIMS = 3
    try:
        _seed_agent_jobs(job_db, 5)
        claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=1000)
    finally:
        select_module.MAX_BATCH_CLAIMS = original
    assert original > 3  # 防测试自身把常量改坏
    assert len(claims) == 3
    assert _queued_count(job_db) == 2


def test_batch_claim_per_pool_limits(job_db) -> None:
    """分池批申请：agent_limit=1 / code_limit=2 时一批恰领 1 agent + 2 code。"""
    _seed_agent_jobs(job_db, 3)
    _seed_code_jobs(job_db, 3)
    _register_worker()

    claims = claim_batch(
        broker(job_db.jobs_dir.parent),
        "worker-1",
        None,
        None,
        limit=10,
        agent_limit=1,
        code_limit=2,
    )

    assert [claim.kind for claim in claims].count("agent") == 1
    assert [claim.kind for claim in claims].count("code") == 2
    assert _queued_count(job_db, kind="agent") == 2
    assert _queued_count(job_db, kind="code") == 1


def test_batch_claim_zero_pool_limit_skips_that_pool(job_db) -> None:
    """agent_limit=0 = 本批不领 agent（#534 越池止血通道的 Host 侧）。"""
    _seed_agent_jobs(job_db, 2)
    _seed_code_jobs(job_db, 2)
    _register_worker()

    claims = claim_batch(
        broker(job_db.jobs_dir.parent),
        "worker-1",
        None,
        None,
        limit=10,
        agent_limit=0,
        code_limit=5,
    )

    assert {claim.kind for claim in claims} == {"code"}
    assert len(claims) == 2
    assert _queued_count(job_db, kind="agent") == 2


def test_batch_claim_respects_workspace_capacity_across_the_batch(job_db) -> None:
    """批内容量记账：workspace 容量 2，批 limit 5 也只能 promote 2 个——
    WorkerView 快照必须随批内 promote 递增，否则批会 overrun。"""
    _seed_agent_jobs(job_db, 5)
    with job_db.connect() as conn:
        conn.execute(
            "update workspace_agent_capacities set max_concurrency=2"
            " where workspace_id='test-workspace'"
        )
    _register_worker()

    claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=5)

    assert len(claims) == 2
    assert _queued_count(job_db) == 3


def test_batch_claim_keeps_prefix_when_a_candidate_races(job_db, monkeypatch) -> None:
    """ClaimRacedError 的 savepoint 容错：第 k 个候选 promote 时 job 已离开
    runnable 集，批保留前 k-1 个并终止，不整体回滚。

    #759 后 awaiting_approval 成为 promote 的合法起点（EXEC-APPROVAL-001
    并行分支可认领），不能再用来构造竞态——改为在第 2 个候选的 promote
    上直接注入 ClaimRacedError（测试主题是 savepoint 容错本身）。
    """
    _seed_agent_jobs(job_db, 3)
    # 队列序：job-0（正常）→ job-1（竞态）→ job-2（不应被领到——批在竞态
    # 处终止）。
    base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    with job_db.connect() as conn:
        for offset in range(3):
            conn.execute(
                "update agent_execution_requests set queued_at=%s where job_id=%s",
                (base + timedelta(seconds=offset), f"job-{offset}"),
            )
    _register_worker()

    import server.app.agent_broker.claim_evaluate as evaluate_module
    from server.app.agent_broker.claim_scan import ClaimRacedError

    original_promote = evaluate_module.promote_claim

    def raced_promote(broker, conn, worker_id, selected, manifest, kind, **kwargs):
        if selected["job_id"] == "job-1":
            raise ClaimRacedError()
        return original_promote(broker, conn, worker_id, selected, manifest, kind, **kwargs)

    monkeypatch.setattr(evaluate_module, "promote_claim", raced_promote)

    claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=3)

    assert [claim.job_id for claim in claims] == ["job-0"]
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select job_id, state from agent_execution_requests order by job_id"
        ).fetchall()
    states = {row["job_id"]: row["state"] for row in rows}
    # 竞态候选回滚到 savepoint：请求保持 queued（未被 cancel、未被 claim），
    # 后继候选本批不再评估。
    assert states == {"job-0": "claimed", "job-1": "queued", "job-2": "queued"}


def test_batch_claim_empty_queue_returns_empty(job_db) -> None:
    _register_worker()
    claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=8)
    assert claims == []


def test_batch_claim_defers_workspace_below_lock_floor(job_db) -> None:
    """EXEC-CLAIM-LOCK-001 批形态（codex P1）：批事务按锁键升序累积
    advisory 锁——队列序靠后的低序 workspace 候选让位到下一批（新事务、
    floor 重置），两个并发批因此共享同一全局锁序，不可能 AB-BA。

    场景：ws-b 的请求排在队首（先领，floor=ws-b），ws-a 的请求同批被
    跳过（batch_lock_order），第二批（本用例的下一次调用）领到。ws-a/
    ws-b 的文本序与 int 锁键序恰好一致（测试内断言钉住对齐——反序对
    形态在 test_batch_claim_floor_follows_actual_lock_key 里）。"""
    seed_request(job_db, job_id="job-b", workspace_id="ws-b")
    seed_request(job_db, job_id="job-a", workspace_id="ws-a")
    # 钉死队列序：ws-b 在前。
    base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set queued_at=%s where job_id='job-b'", (base,)
        )
        conn.execute(
            "update agent_execution_requests set queued_at=%s where job_id='job-a'",
            (base + timedelta(seconds=1),),
        )
        keys = {
            str(row["workspace_id"]): int(row["k"])
            for row in conn.execute(
                "select workspace_id, hashtext('agent-ws:' || workspace_id)::int as k"
                " from jobs where id in ('job-a', 'job-b')"
            ).fetchall()
        }
    assert keys["ws-a"] < keys["ws-b"], "fixture ids must keep text and lock-key order aligned"
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    first = claim_batch(pool, "worker-1", None, None, limit=4)
    second = claim_batch(pool, "worker-1", None, None, limit=4)

    assert [claim.workspace_id for claim in first] == ["ws-b"]
    assert [claim.workspace_id for claim in second] == ["ws-a"]


def test_batch_claim_floor_follows_actual_lock_key(job_db) -> None:
    """ws_lock_floor 比较实际 agent-ws capacity 锁键
    （hashtext('agent-ws:' || workspace_id)::int），不是 workspace 文本——
    文本序与 int 锁键序相反的 ws 对（约一半 id 对如此，无需碰撞）上，
    文本域的 floor 会让「文本较小、锁键较大」之后的候选下探，破坏批内
    升序纪律。钉子：播种一对反序 id，队首（文本较小、锁键较大）先领后，
    文本较大但锁键更小的候选必须被让位到下一批——同
    test_try_claim_many_orders_by_actual_ws_lock_key 的 agent 批形态。"""
    # 播种一对「文本序与 hashtext int 序相反」的 workspace id。
    pool_ids: list[tuple[str, int]] = []
    with job_db.connect() as conn:
        for i in range(200):
            wid = f"ws-f{i:03d}"
            row = conn.execute("select hashtext('agent-ws:' || %s)::int as k", (wid,)).fetchone()
            assert row is not None
            pool_ids.append((wid, int(row["k"])))
    ws_low, ws_high = "", ""
    for wid, key in sorted(pool_ids, key=lambda p: p[0]):
        smaller = [w for w, other_key in pool_ids if w > wid and other_key < key]
        if smaller:
            ws_low = wid
            ws_high = min(smaller)
            break
    assert ws_low and ws_high, "expected an inverted (text, lock-key) pair among 200 candidates"

    seed_request(job_db, job_id="job-lo", workspace_id=ws_low)
    seed_request(job_db, job_id="job-hi", workspace_id=ws_high)
    # 钉死队列序：文本较小（job-lo）在前；并断言反序对成立（其锁键更大）。
    base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set queued_at=%s where job_id='job-lo'", (base,)
        )
        conn.execute(
            "update agent_execution_requests set queued_at=%s where job_id='job-hi'",
            (base + timedelta(seconds=1),),
        )
        keys = {
            str(row["workspace_id"]): int(row["k"])
            for row in conn.execute(
                "select workspace_id, hashtext('agent-ws:' || workspace_id)::int as k"
                " from jobs where id in ('job-lo', 'job-hi')"
            ).fetchall()
        }
    assert keys[ws_low] > keys[ws_high], "fixture must invert text vs lock-key order"
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    first = claim_batch(pool, "worker-1", None, None, limit=4)
    second = claim_batch(pool, "worker-1", None, None, limit=4)

    # 第一批：job-lo 领走；job-hi 的锁键更小（文本域会判「升序可领」），
    # 必须按 int 域 floor 让位到第二批。
    assert [claim.workspace_id for claim in first] == [ws_low]
    assert [claim.workspace_id for claim in second] == [ws_high]


def test_code_candidates_ignore_agent_workspace_lock_floor(job_db) -> None:
    """Code claims never enter the agent-ws capacity-lock domain."""
    _seed_code_jobs(job_db, 1, prefix="code-floor")
    with job_db.connect() as conn:
        code_key = int(
            conn.execute("select hashtext('agent-ws:test-workspace')::int as k").fetchone()["k"]
        )
        agent_workspace = next(
            workspace_id
            for workspace_id in (f"agent-floor-{i}" for i in range(5000))
            if int(
                conn.execute(
                    "select hashtext('agent-ws:' || %s)::int as k", (workspace_id,)
                ).fetchone()["k"]
            )
            > code_key
        )
    seed_request(job_db, job_id="agent-floor-job", workspace_id=agent_workspace)
    _register_worker()

    claims = claim_batch(
        broker(job_db.jobs_dir.parent),
        "worker-1",
        None,
        None,
        limit=2,
        agent_limit=1,
        code_limit=1,
    )

    # 写入段的稳定锁序（EXEC-GENERATION-001 #645 P2：SAVEPOINT 不释放
    # advisory xact 锁）把 code 候选与 agent 候选统一按 (ws_lock_key,
    # job_id) 排序；本测试只钉「code 候选不被 agent-ws floor 过滤」——
    # 两类在同一批都领到。
    assert sorted(claim.kind for claim in claims) == ["agent", "code"]


def test_batch_claim_retries_once_on_deadlock(job_db, monkeypatch) -> None:
    """40P01 策略与单条路径一致（claim_retry）：一次立即重试、两阶段都在新
    连接上重估；第二次 40P01 上抛（路由 500 / Worker 退避）。#555 起选择段
    先行（只读、不死锁），此处 mock 的是写入段，选择段跑真——需注册
    Worker。"""
    import psycopg

    import server.app.agent_broker.claim_batch as batch_module

    _register_worker()

    class _Deadlock(psycopg.Error):
        sqlstate = "40P01"

    attempts = 0

    def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _Deadlock("deadlock detected")
        return batch_module.BatchClaimOutcome((), _view(), {})

    monkeypatch.setattr(batch_module, "claim_batch_in_transaction", flaky)
    pool = broker(job_db.jobs_dir.parent)

    assert batch_module.claim_batch_with_retry(pool, "worker-1", None, None, limit=4).claims == ()
    assert attempts == 2

    def always_deadlock(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _Deadlock("deadlock detected")

    monkeypatch.setattr(batch_module, "claim_batch_in_transaction", always_deadlock)
    with pytest.raises(_Deadlock):
        batch_module.claim_batch_with_retry(pool, "worker-1", None, None, limit=4)


def _view():  # type: ignore[no-untyped-def]
    from server.app.agent_broker.claim_scan import WorkerView

    return WorkerView(runtimes=set(), models=set(), labels={}, allowed_workspaces=set())


def test_batch_claim_scan_skipped_when_pools_full(job_db) -> None:
    """两池都满 = 不扫描直接空批（与单条路径的 scan_skipped 分支同语义）。"""
    _register_worker(max_concurrency=1, max_code_concurrency=0)
    _seed_agent_jobs(job_db, 2)
    pool = broker(job_db.jobs_dir.parent)
    assert pool.claim("worker-1") is not None  # 占满 agent 池

    claims = claim_batch(pool, "worker-1", None, None, limit=8)

    assert claims == []
    assert _queued_count(job_db) == 1


def test_promote_folds_queue_wait_into_claim_profile(job_db) -> None:
    """#551：promote 成功时 queue_wait（queued_at→promote）折进 claim 画像族
    ——单条与批（#546）共用 claim_promote.promote_claim，两路都计入。"""
    from server.app.services.runtime_profile import profile

    _seed_agent_jobs(job_db, 2)
    _register_worker()
    before = profile.counters.claim_queue_wait_seconds_total

    claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=2)

    assert len(claims) == 2
    # 批内每个 promote 各折一条：total 增量 > 0 且 max >= 任一增量。
    assert profile.counters.claim_queue_wait_seconds_total > before
    assert profile.counters.claim_queue_wait_seconds_max > 0
