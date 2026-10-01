"""sweep_expired_claims 的 execution-generation CAS（#759 阶段 1d）。

钉住 EXEC-GENERATION-001 的清扫侧语义：对 expired claimed 请求，lease
删除与 node_run 落库照常；但请求落戳代次 != jobs 现值时（reset 后的迟到
清扫），job_nodes/jobs 回写一律跳过，请求按取消收尾而不是重排（新代次
的调度会重新入队，requeue 旧请求会双跑）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection, write_transaction
from tests.helpers.agent_worker_api import broker as _make_broker
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL

_TTL = 90
_WORKER_ID = "worker-sweep-gen"


def _register_worker() -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def _make_worker_stale(age_seconds: float = 300) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update agent_workers set last_seen_at=%s where worker_id=%s",
            (datetime.now(UTC) - timedelta(seconds=age_seconds), _WORKER_ID),
        )


def _silence_request(execution_id: str, seconds: float) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=seconds), execution_id),
        )


def test_sweep_stale_generation_cancels_request_without_touching_job_nodes(job_db) -> None:
    """旧代次迟到清扫：lease 删除 + node_run failed 照常；job_nodes 不动，请求取消。"""
    seed_request(job_db, job_id="job-sweep-stale")
    _register_worker()
    broker = _make_broker(job_db.jobs_dir.parent, lease_ttl_seconds=_TTL)
    claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    _silence_request(claimed.execution_id, _TTL + 10)
    _make_worker_stale()
    # 模拟 mutation 侧 bump（不重置节点——清扫侧 CAS 正是这个缺口的兜底）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("job-sweep-stale",),
        )

    assert broker.sweep_expired_claims() == []

    with read_connection(TEST_DATABASE_URL) as conn:
        request = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
        lease = conn.execute(
            "select count(*) as cnt from executor_leases where id=%s", (claimed.lease_id,)
        ).fetchone()
        run = conn.execute(
            "select status from node_runs where id=%s", (claimed.node_run_id,)
        ).fetchone()
    assert request["state"] == "cancelled"
    assert int(lease["cnt"]) == 0
    assert run["status"] == "failed"
    node = job_db.get_job_node("job-sweep-stale", "generate")
    assert node["status"] == "running"  # 旧代次清扫绝不翻转节点行


def test_sweep_matching_generation_requeues_as_before(job_db) -> None:
    """代次相符的清扫：行为不变——lease 删除、节点回 pending、请求重排 queued。"""
    seed_request(job_db, job_id="job-sweep-fresh")
    _register_worker()
    broker = _make_broker(job_db.jobs_dir.parent, lease_ttl_seconds=_TTL)
    claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    _silence_request(claimed.execution_id, _TTL + 10)
    _make_worker_stale()

    assert broker.sweep_expired_claims() == [claimed.execution_id]

    with read_connection(TEST_DATABASE_URL) as conn:
        request = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
    assert request["state"] == "queued"
    node = job_db.get_job_node("job-sweep-fresh", "generate")
    assert node["status"] == "pending"
