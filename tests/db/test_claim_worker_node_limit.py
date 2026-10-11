"""Worker 节点级并发上限（issue #1158）：每台机器按裸 node_key 的执行容量。

两层并发模型的第二层：workspace_node_limits（#1149）是跨全部机器合并计数
的 workspace 全局上限；本文件钉住的是 (worker_id, node_key) 的机器资源保
护层——Worker 每次 claim 重声明 {node_key: N}（sync_declared_capacity 热
同步，无需重注册），Host 在 evaluate_candidate 里按本机 claimed 请求行计
数强制（不分 kind），超限 skip（worker_node_limit_full）留队列。

验收面对应：

1. 弱机器声明 {heavy: 1}，两台机器并发 claim 同节点（workspace 上限 4）：
   弱机器第二个 skip 留队列，强机器（未声明）不受影响——单机单节点有效
   上限 = min(worker 声明上限, workspace 全局预算余量）由两道独立的门叠加
   得出，无联动；
2. 声明热生效：claim 声明即同步进 agent_workers 库存列并在同一 claim 内
   生效；None（不声明）保留库存值，显式 {} 清空（无限制）；
3. 未声明 / 空 map 时行为与现状完全一致（同节点两请求都可领取，零回归面）；
4. 计数不分 kind：agent 与 code 的 claimed 行都占本机该节点的额度；
5. 批 claim 同节点多候选：批写阶段逐候选重跑 evaluate，批内前序 promote
   的 claimed 翻转同事务可见，第二个候选 skip、第一个保留。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim_batch import claim_batch_with_retry
from server.app.agent_control.registry import AgentWorkerRegistry
from shared.protocol import PROTOCOL_VERSION
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL


def _register_worker(worker_id: str) -> None:
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


def _set_node_limit(job_db, workspace_id: str, node_key: str, limit: int) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_limits(workspace_id, node_key, concurrency_limit)"
            " values (%s, %s, %s)"
            " on conflict(workspace_id, node_key) do update set"
            " concurrency_limit=excluded.concurrency_limit",
            (workspace_id, node_key, limit),
        )


def _enqueue_code(
    job_db,
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    order: int = 0,
) -> str:
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


def _stored_node_limits(job_db, worker_id: str) -> dict[str, Any]:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select node_concurrency_limits_json from agent_workers where worker_id=%s",
            (worker_id,),
        ).fetchone()
    assert row is not None
    return dict(json.loads(str(row["node_concurrency_limits_json"])))


def test_weak_worker_capped_while_strong_worker_claims_same_node(job_db) -> None:
    """验收 1：弱机器声明 {heavy: 1}，workspace 全局上限 4——弱机器第二个
    同节点请求 skip（worker_node_limit_full）留队列；强机器未声明、不受
    影响照常领取（workspace 层的 lease 计数 1 < 4 放行）。"""
    workspace_id, node_key = "ws-1158-a", "heavy"
    _seed_code_job(job_db, workspace_id, "job-a1", node_key)
    _seed_code_job(job_db, workspace_id, "job-a2", node_key)
    _set_node_limit(job_db, workspace_id, node_key, 4)
    _enqueue_code(job_db, workspace_id, "job-a1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-a2", node_key, order=1)
    _register_worker("worker-1158-weak")
    _register_worker("worker-1158-strong")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    weak1 = claim_batch_with_retry(
        pool,
        "worker-1158-weak",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in weak1.claims] == ["job-a1"]

    weak2 = claim_batch_with_retry(
        pool,
        "worker-1158-weak",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert weak2.claims == ()
    assert weak2.skip_reasons.get("worker_node_limit_full") == 1
    assert _request_state(job_db, second) == "queued"  # 留队列，未被取消

    strong = claim_batch_with_retry(pool, "worker-1158-strong", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in strong.claims] == ["job-a2"]
    assert _request_state(job_db, second) == "claimed"


def test_node_limit_declaration_hot_syncs_and_enforces_immediately(job_db) -> None:
    """验收 2：声明热生效——claim 携带的映射同步进 agent_workers 库存列并
    在同一 claim 的判定里生效；None（不声明）保留库存值，显式 {} 清空。"""
    workspace_id, node_key = "ws-1158-b", "heavy"
    for index in range(3):
        _seed_code_job(job_db, workspace_id, f"job-b{index}", node_key)
        _enqueue_code(job_db, workspace_id, f"job-b{index}", node_key, order=index)
    _register_worker("worker-1158-b")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    round1 = claim_batch_with_retry(
        pool,
        "worker-1158-b",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in round1.claims] == ["job-b0"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 1}

    # None = 不声明：库存值保留并继续生效（计数 1 >= 1 → skip）。
    round2 = claim_batch_with_retry(pool, "worker-1158-b", None, None, limit=1, code_limit=1)
    assert round2.claims == ()
    assert round2.skip_reasons.get("worker_node_limit_full") == 1
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 1}

    # 显式 {} = 清空：同一 claim 即按新映射放行，库存列同步为 {}。
    round3 = claim_batch_with_retry(
        pool, "worker-1158-b", None, None, limit=1, code_limit=1, declared_node_limits={}
    )
    assert [claim.job_id for claim in round3.claims] == ["job-b1"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {}

    # 收紧→放宽的热更同样在声明的同一 claim 生效：计数 2 < 3 → 放行。
    round4 = claim_batch_with_retry(
        pool,
        "worker-1158-b",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 3},
    )
    assert [claim.job_id for claim in round4.claims] == ["job-b2"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 3}


def test_undeclared_and_empty_map_stay_unlimited(job_db) -> None:
    """验收 3（零回归面）：未声明（旧 Worker）与显式空 map 都不设限——
    同节点两个请求照常全部领取，skip_reasons 无 worker_node_limit_full。"""
    workspace_id, node_key = "ws-1158-c", "package"
    for index in range(3):
        _seed_code_job(job_db, workspace_id, f"job-c{index}", node_key)
        _enqueue_code(job_db, workspace_id, f"job-c{index}", node_key, order=index)
    _register_worker("worker-1158-legacy")
    _register_worker("worker-1158-empty")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    legacy = claim_batch_with_retry(pool, "worker-1158-legacy", None, None, limit=2, code_limit=2)
    assert [claim.job_id for claim in legacy.claims] == ["job-c0", "job-c1"]
    assert "worker_node_limit_full" not in legacy.skip_reasons

    empty = claim_batch_with_retry(
        pool, "worker-1158-empty", None, None, limit=1, code_limit=1, declared_node_limits={}
    )
    assert [claim.job_id for claim in empty.claims] == ["job-c2"]
    assert "worker_node_limit_full" not in empty.skip_reasons
    assert _stored_node_limits(job_db, "worker-1158-legacy") == {}
    assert _stored_node_limits(job_db, "worker-1158-empty") == {}


def test_worker_node_limit_counts_both_kinds(job_db) -> None:
    """计数不分 kind：机器资源保护覆盖任何在本机烧 CPU 的执行——同
    node_key 的 agent 请求被认领后，同 key 的 code 请求撞 {key: 1} skip。"""
    workspace_id, node_key = "ws-1158-d", "review"
    seed_request(job_db, job_id="job-d-agent", workspace_id=workspace_id, node_key=node_key)
    _seed_code_job(job_db, workspace_id, "job-d-code", node_key)
    _enqueue_code(job_db, workspace_id, "job-d-code", node_key)
    _register_worker("worker-1158-d")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 第一轮只开 agent 池：agent 请求被认领，占去本机该节点 1 个额度。
    round1 = claim_batch_with_retry(
        pool,
        "worker-1158-d",
        None,
        None,
        limit=1,
        agent_limit=1,
        code_limit=0,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in round1.claims] == ["job-d-agent"]

    # 第二轮只开 code 池：同 key 的 code 请求按不分 kind 的计数 skip。
    round2 = claim_batch_with_retry(
        pool,
        "worker-1158-d",
        None,
        None,
        limit=1,
        agent_limit=0,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert round2.claims == ()
    assert round2.skip_reasons.get("worker_node_limit_full") == 1


def test_batch_second_candidate_same_node_skips(job_db) -> None:
    """批 claim（#546/#555）同节点两候选：批写阶段逐候选重跑 evaluate，
    前序 promote 的 claimed 翻转同事务可见——第二个候选 skip
    （worker_node_limit_full），第一个 claim 保留（skip-and-continue）。"""
    workspace_id, node_key = "ws-1158-e", "heavy"
    _seed_code_job(job_db, workspace_id, "job-e1", node_key)
    _seed_code_job(job_db, workspace_id, "job-e2", node_key)
    first = _enqueue_code(job_db, workspace_id, "job-e1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-e2", node_key, order=1)
    _register_worker("worker-1158-e")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    outcome = claim_batch_with_retry(
        pool,
        "worker-1158-e",
        None,
        None,
        limit=2,
        code_limit=2,
        declared_node_limits={node_key: 1},
    )

    assert [claim.job_id for claim in outcome.claims] == ["job-e1"]
    assert outcome.skip_reasons.get("worker_node_limit_full") == 1
    assert _request_state(job_db, first) == "claimed"
    assert _request_state(job_db, second) == "queued"
