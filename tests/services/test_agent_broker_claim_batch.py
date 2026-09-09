"""Batch claim transaction tests (issue #546; #555 two-phase split).

``claim_batch`` selects candidates on a read-only connection
(``claim_batch_select``) and promotes them in ONE compact write transaction
(``claim_batch_tx``): per-pool caps (agent_limit/code_limit), the mid-batch
capacity accounting (the WorkerView snapshot must be incremented per
promote), the SAVEPOINT containment of ClaimRacedError (first k claims
survive a raced candidate), the empty/scan-skipped verdicts, and the #555
write-phase revalidation of a stale selection. The single-claim path
(``broker.claim``) is covered byte-for-byte by the pre-#546 suites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from server.app.agent_broker.claim_batch import claim_batch
from server.app.agent_broker.claim_batch_select import select_batch_candidates
from server.app.agent_broker.claim_batch_tx import claim_batch_in_transaction
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
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


def test_batch_claim_keeps_prefix_when_a_candidate_races(job_db) -> None:
    """ClaimRacedError 的 savepoint 容错：第 k 个候选 promote 时 job 已离开
    runnable 集（此处用 awaiting_approval 构造——重检放行、promote 条件不
    含它），批保留前 k-1 个并终止，不整体回滚。"""
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
        conn.execute("update jobs set status='awaiting_approval' where id='job-1'")
    _register_worker()

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
    """EXEC-CLAIM-LOCK-001 批形态（codex P1）：批事务按 workspace 升序累积
    advisory 锁——队列序靠后的低序 workspace 候选让位到下一批（新事务、
    floor 重置），两个并发批因此共享同一全局锁序，不可能 AB-BA。

    场景：ws-b 的请求排在队首（先领，floor=ws-b），ws-a 的请求同批被
    跳过（batch_lock_order），第二批（本用例的下一次调用）领到。"""
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
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    first = claim_batch(pool, "worker-1", None, None, limit=4)
    second = claim_batch(pool, "worker-1", None, None, limit=4)

    assert [claim.workspace_id for claim in first] == ["ws-b"]
    assert [claim.workspace_id for claim in second] == ["ws-a"]


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


# ---------------------------------------------------------------------------
# #555：两阶段拆分与锁面收窄的回归钉子
# ---------------------------------------------------------------------------


def test_batch_write_phase_revalidates_stale_selection_and_never_scans(job_db) -> None:
    """#555 竞态窗口的重校验：只读选择段选出候选后、写入段开始前，候选被
    竞争者领走——写入段重跑 evaluate_candidate（SKIP LOCKED 探针 +
    条件 promote），判 lock_raced 跳过并继续后续候选，绝不半应用。

    同钉结构性不变量：写入事务内不得出现扫描语句（eligible_workspaces
    只属于选择段）——锁窗口 O(批×扫描) 的回归会直接亮红灯。"""
    _seed_agent_jobs(job_db, 2)
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    selection = select_batch_candidates(pool, "worker-1", None, None, limit=2)
    assert [str(row["job_id"]) for row in selection.candidates] == ["job-0", "job-1"]

    # 竞争者介入（选择 → 写入之间）：job-0 被领走且已提交。
    raced = pool.claim("worker-1")
    assert raced is not None and raced.job_id == "job-0"

    scan_statements: list[str] = []
    with write_transaction(TEST_DATABASE_URL) as conn:
        real_execute = conn.execute

        def spy(sql: Any, params: Any = None) -> Any:
            if "eligible_workspaces" in str(sql):
                scan_statements.append(str(sql))
            return real_execute(sql, params)

        conn.execute = spy  # type: ignore[method-assign]
        outcome = claim_batch_in_transaction(
            pool, conn, "worker-1", None, None, selection=selection
        )

    assert scan_statements == []
    assert [claim.job_id for claim in outcome.claims] == ["job-1"]
    assert outcome.skip_reasons.get("lock_raced") == 1


def _seed_two_node_job(job_db) -> None:
    """同一 job 上两个可 claim 的节点（generate + review）——多节点 job 的
    后继 claim 形态（#555 修法 2 的靶场景）。"""
    seed_request(job_db, job_id="job-multi", node_key="generate")
    # 同 job 的第二个节点：补 node 行 + 路由 + 入队（Agent 定义同
    # seed_request 的默认值，hash 必须一致才能过 claim 匹配）。
    from server.app.agent_broker import AgentExecutionRequest
    from server.app.agent_catalog import AgentDefinition

    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    with job_db.connect() as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values ('job-multi', 'review')")
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values ('test-workspace', 'review', 'agent', 'generator-v1')"
        )
    assert broker(job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id="job-multi",
            workflow_key="questions",
            node_key="review",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": "job-multi",
                "log_path": "logs/job-multi-review.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
        )
    )


def test_batch_claim_does_not_rewrite_already_running_jobs_row(job_db) -> None:
    """#555 修法 2：同 job 后继节点的 claim 不再重写已 running 的 jobs 行
    （旧版 status in ('queued','running') 的无差别 UPDATE 每个节点重锁
    同一热行）。updated_at 钉在已知时刻，第二次 claim 后纹丝不动 = 未写；
    而 job 离开 runnable 集仍判 raced（由既有 awaiting_approval 用例钉住）。
    """
    _seed_two_node_job(job_db)
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    first = claim_batch(pool, "worker-1", None, None, limit=1)
    assert [claim.node_key for claim in first] == ["generate"]
    with job_db.connect() as conn:
        row = conn.execute("select status from jobs where id='job-multi'").fetchone()
        assert row["status"] == "running"
        # 钉死 updated_at：若第二次 claim 重写 jobs 行，该值会被刷新。
        conn.execute("update jobs set updated_at='2020-01-01 00:00:00+00' where id='job-multi'")

    second = claim_batch(pool, "worker-1", None, None, limit=1)
    assert [claim.node_key for claim in second] == ["review"]

    with job_db._connect_read() as conn:
        row = conn.execute("select status, updated_at from jobs where id='job-multi'").fetchone()
    assert row["status"] == "running"
    # 连接层 row 工厂可能给 str（datetime.fromisoformat 归一，同
    # claim_promote._queue_wait_seconds 的先例）。
    assert datetime.fromisoformat(str(row["updated_at"])).year == 2020


def test_batch_claim_same_batch_second_node_promote_is_noop_write(job_db) -> None:
    """修法 2 的批内形态：同批领同 job 的两个节点——第一节点的 promote 写
    jobs 行（rowcount=1，queued→running），第二节点的收窄 UPDATE 不再匹配
    （rowcount=0），重读判 running 放行、不重写。"""
    _seed_two_node_job(job_db)
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    selection = select_batch_candidates(pool, "worker-1", None, None, limit=2)
    assert len(selection.candidates) == 2

    jobs_update_rowcounts: list[int] = []
    with write_transaction(TEST_DATABASE_URL) as conn:
        real_execute = conn.execute

        def spy(sql: Any, params: Any = None) -> Any:
            cursor = real_execute(sql, params)
            if "update jobs set status='running'" in str(sql):
                jobs_update_rowcounts.append(cursor.rowcount)
            return cursor

        conn.execute = spy  # type: ignore[method-assign]
        outcome = claim_batch_in_transaction(
            pool, conn, "worker-1", None, None, selection=selection
        )

    assert len(outcome.claims) == 2
    assert jobs_update_rowcounts == [1, 0]


def _set_worker_last_seen(job_db, sql_expression: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            f"update agent_workers set last_seen_at={sql_expression} where worker_id='worker-1'"
        )


def _worker_last_seen(job_db) -> Any:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select last_seen_at from agent_workers where worker_id='worker-1'"
        ).fetchone()
    return row["last_seen_at"]


def test_claim_touch_throttled_within_interval_and_writes_when_stale(job_db) -> None:
    """#555 修法 3：claim 的 last_seen_at 写入按间隔节流——值还新鲜（<30s
    默认窗口）时不重写热行；越过窗口仍刷新（节流不是停写，活性由
    heartbeat / authenticate 的 WorkerLiveness 覆盖）。"""
    _seed_agent_jobs(job_db, 2)
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)

    _set_worker_last_seen(job_db, "current_timestamp - interval '5 seconds'")
    fresh = _worker_last_seen(job_db)
    assert len(claim_batch(pool, "worker-1", None, None, limit=1)) == 1
    assert _worker_last_seen(job_db) == fresh

    _set_worker_last_seen(job_db, "current_timestamp - interval '1 hour'")
    stale = _worker_last_seen(job_db)
    assert len(claim_batch(pool, "worker-1", None, None, limit=1)) == 1
    assert _worker_last_seen(job_db) > stale


def test_mark_done_touch_throttled_and_zero_interval_kill_switch(job_db) -> None:
    """mark_done 同样节流（result commit 不再过 agent_workers 热行）；
    touch_worker_interval_seconds=0 恢复每次写（0.7.5 行为，A/B 止血位）。"""
    _seed_agent_jobs(job_db, 2)
    _register_worker()
    pool = broker(job_db.jobs_dir.parent)
    claims = claim_batch(pool, "worker-1", None, None, limit=2)
    assert len(claims) == 2

    _set_worker_last_seen(job_db, "current_timestamp - interval '5 seconds'")
    fresh = _worker_last_seen(job_db)
    done = pool.mark_done(claims[0].execution_id, "worker-1", claims[0].lease_id, {})
    assert done is not None
    assert _worker_last_seen(job_db) == fresh

    _set_worker_last_seen(job_db, "current_timestamp - interval '1 hour'")
    stale = _worker_last_seen(job_db)
    done = pool.mark_done(claims[1].execution_id, "worker-1", claims[1].lease_id, {})
    assert done is not None
    assert _worker_last_seen(job_db) > stale

    # 止血位：interval=0 时新鲜值也照写。
    _seed_agent_jobs(job_db, 1, prefix="job-zero")
    pool_zero = broker(job_db.jobs_dir.parent, touch_worker_interval_seconds=0)
    _set_worker_last_seen(job_db, "current_timestamp - interval '5 seconds'")
    fresh = _worker_last_seen(job_db)
    assert len(claim_batch(pool_zero, "worker-1", None, None, limit=1)) == 1
    assert _worker_last_seen(job_db) > fresh
