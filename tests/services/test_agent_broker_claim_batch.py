"""Batch claim transaction tests (issue #546).

``claim_batch`` promotes up to ``limit`` executions in ONE write transaction:
per-pool caps (agent_limit/code_limit), the mid-batch capacity accounting
(the WorkerView snapshot must be incremented per promote), the SAVEPOINT
containment of ClaimRacedError (first k claims survive a raced candidate),
and the empty/scan-skipped verdicts. The single-claim path
(``broker.claim``) is covered byte-for-byte by the pre-#546 suites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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
    from server.app.agent_broker.claim_batch import MAX_BATCH_CLAIMS

    _register_worker()
    # 不真造 257 个 job——直接把上限常量缩小验证 clamp 路径。
    import server.app.agent_broker.claim_batch as claim_batch_module

    original = claim_batch_module.MAX_BATCH_CLAIMS
    claim_batch_module.MAX_BATCH_CLAIMS = 3
    try:
        _seed_agent_jobs(job_db, 5)
        claims = claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=1000)
    finally:
        claim_batch_module.MAX_BATCH_CLAIMS = original
    assert MAX_BATCH_CLAIMS > 3  # 防测试自身把常量改坏
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


def test_batch_claim_scan_skipped_when_pools_full(job_db) -> None:
    """两池都满 = 不扫描直接空批（与单条路径的 scan_skipped 分支同语义）。"""
    _register_worker(max_concurrency=1, max_code_concurrency=0)
    _seed_agent_jobs(job_db, 2)
    pool = broker(job_db.jobs_dir.parent)
    assert pool.claim("worker-1") is not None  # 占满 agent 池

    claims = claim_batch(pool, "worker-1", None, None, limit=8)

    assert claims == []
    assert _queued_count(job_db) == 1
