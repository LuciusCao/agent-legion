"""远程 code claim 的节点级并发限制（issue #1149）。

修复前 workspace_node_limits 只在本地 code 池路径生效
（executors/_lease_claim_limits.check_claim_capacity）；远程路径
（agent_broker/claim_evaluate.evaluate_candidate 的 code 分支）没有任何
(workspace_id, node_key) 检查，同一节点被跨 job 并行 claim。本文件钉住
修复后的三个面：

1. 跨 job 同节点：limit=1 时第二个请求 skip（node_limit_full）留队列，
   第一个完成后（mark_done 收尾 lease）第三轮 claim 放行；
2. 本地/远程混合计数：本地池 lease（executor_id='code'）占位时远程
   claim 被拒——计数不筛 executor_id，同一张 executor_leases 表合并；
3. 批 claim（#546/#555）同节点多候选：批写阶段重跑 evaluate，批内
   第二个候选 skip，第一个 claim 保留（savepoint 语义不受影响）。

串行搭建（无交错线程）：每轮 claim 是独立提交的事务，计数在提交后
对下一轮可见，无需 pg_locks 同步点。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim_batch import claim_batch_with_retry
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import CODE_EXECUTOR_ID, ExecutionResult, LeaseClaimRequest
from shared.protocol import PROTOCOL_VERSION
from tests.postgres_support import TEST_DATABASE_URL


def _register_code_worker(worker_id: str) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        capabilities=["package"],
        models=[{"provider": "gateway", "model": "test-model", "runtime": "pi"}],
        max_concurrency=10,
        max_code_concurrency=10,
        labels={"arch": "arm64"},
        protocol_version=PROTOCOL_VERSION,
    )


def _seed_code_lane(
    job_db, workspace_id: str, node_key: str, *, limit: int, job_ids: list[str]
) -> None:
    """workspace + jobs + node 行 + 节点 limit（limit 行有 FK 到 workspaces）。"""
    for job_id in job_ids:
        _seed_code_job(job_db, workspace_id, job_id, node_key)
    _set_node_limit(job_db, workspace_id, node_key, limit)


def _set_node_limit(job_db, workspace_id: str, node_key: str, limit: int) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_limits(workspace_id, node_key, concurrency_limit)"
            " values (%s, %s, %s)"
            " on conflict(workspace_id, node_key) do update set"
            " concurrency_limit=excluded.concurrency_limit",
            (workspace_id, node_key, limit),
        )


def _seed_code_job(job_db, workspace_id: str, job_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _enqueue_code(job_db, workspace_id: str, job_id: str, node_key: str, *, order: int = 0) -> str:
    execution_id = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key=workspace_id,
            node_key=node_key,
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}-{node_key}.log",
                "config": {"mode": "fast"},
            },
            kind="code",
        )
    )
    assert execution_id is not None
    # queued_at 钉死队列序（同毫秒入队时 current_timestamp 无序）。
    queued_at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC) + timedelta(seconds=order)
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set queued_at=%s where execution_id=%s",
            (queued_at, execution_id),
        )
    return execution_id


def _request_state(job_db, execution_id: str) -> str:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"])


def _active_node_lease_count(job_db, workspace_id: str, node_key: str) -> int:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select count(*) as cnt from executor_leases"
            " where workspace_id=%s and node_key=%s and status='active'"
            " and expires_at>current_timestamp",
            (workspace_id, node_key),
        ).fetchone()
    assert row is not None
    return int(row["cnt"])


def _local_claim_request(
    workspace_id: str, job_id: str, node_key: str, limit: int
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=CODE_EXECUTOR_ID,
        global_capacity=4,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key=workspace_id,
        node_key=node_key,
        capability="package",
        local_node_limit=limit,
        lease_ttl_seconds=60,
        log_path=f"logs/{job_id}-{node_key}.log",
    )


def test_remote_node_limit_skips_second_job_until_first_completes(job_db) -> None:
    """用例 1：limit=1、同节点跨 job 两请求——第二个 skip 留队列
    （skip_reasons 计 node_limit_full、非 cancel），第一个 mark_done 收尾
    lease 后第三轮 claim 放行：限制是容量门不是死锁。"""
    workspace_id, node_key = "ws-1149-a", "package"
    _seed_code_lane(job_db, workspace_id, node_key, limit=1, job_ids=["job-a", "job-b"])
    first = _enqueue_code(job_db, workspace_id, "job-a", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-b", node_key, order=1)
    _register_code_worker("worker-1149-a")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    round1 = claim_batch_with_retry(pool, "worker-1149-a", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round1.claims] == ["job-a"]
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 1

    round2 = claim_batch_with_retry(pool, "worker-1149-a", None, None, limit=1, code_limit=1)
    assert round2.claims == ()
    assert round2.skip_reasons.get("node_limit_full") == 1
    assert _request_state(job_db, second) == "queued"  # 留队列，未被取消

    # 第一个完成：finish 收尾 lease（released、节点计数归零）+ mark_done
    # 关闭请求行——与真实 result commit 的收尾序列一致。
    claim = round1.claims[0]
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert repo.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
    assert pool.mark_done(claim.execution_id, "worker-1149-a", claim.lease_id, {}) is not None
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 0

    round3 = claim_batch_with_retry(pool, "worker-1149-a", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round3.claims] == ["job-b"]
    assert _request_state(job_db, first) == "done"


def test_remote_claim_blocked_by_local_pool_lease(job_db) -> None:
    """用例 2：本地/远程混合计数——本地 code 池 lease（executor_id='code'）
    占位时，远程 claim 被同一 (workspace, node) 计数拒绝（单条 claim 路径，
    请求留队列）；计数不筛 executor_id 是合并计数的语义核心。"""
    workspace_id, node_key = "ws-1149-b", "review"
    _seed_code_lane(job_db, workspace_id, node_key, limit=1, job_ids=["job-local", "job-remote"])
    _enqueue_code(job_db, workspace_id, "job-remote", node_key)
    _register_code_worker("worker-1149-b")
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    local = repo.try_claim(_local_claim_request(workspace_id, "job-local", node_key, 1))
    assert local is not None  # 本地路径占用唯一名额
    with job_db._connect_read() as conn:
        executor_ids: list[str] = [
            str(row["executor_id"])
            for row in conn.execute(
                "select executor_id from executor_leases where job_id=%s", ("job-local",)
            ).fetchall()
        ]
    assert executor_ids == [CODE_EXECUTOR_ID]

    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert pool.claim("worker-1149-b") is None  # 远程路径被合并计数拒绝
    with job_db._connect_read() as conn:
        row: Any = conn.execute(
            "select state from agent_execution_requests where job_id='job-remote'"
        ).fetchone()
    assert row is not None
    assert row["state"] == "queued"


def test_batch_claim_skips_second_candidate_of_same_node(job_db) -> None:
    """用例 3：批 claim（#546/#555）同节点两候选——写阶段逐候选重跑
    evaluate，批内第一个 promote、第二个 skip（node_limit_full），批保留
    第一个 claim（skip-and-continue，不触发 savepoint 回滚链）。"""
    workspace_id, node_key = "ws-1149-c", "package"
    _seed_code_lane(job_db, workspace_id, node_key, limit=1, job_ids=["job-c1", "job-c2"])
    first = _enqueue_code(job_db, workspace_id, "job-c1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-c2", node_key, order=1)
    _register_code_worker("worker-1149-c")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    outcome = claim_batch_with_retry(pool, "worker-1149-c", None, None, limit=2, code_limit=2)

    assert [claim.job_id for claim in outcome.claims] == ["job-c1"]
    assert outcome.skip_reasons.get("node_limit_full") == 1
    assert _request_state(job_db, first) == "claimed"
    assert _request_state(job_db, second) == "queued"
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 1
