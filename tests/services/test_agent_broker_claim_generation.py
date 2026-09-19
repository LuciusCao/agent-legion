"""Agent/远端 claim 路径的 execution-generation CAS（#759 阶段 1c）。

钉住 EXEC-GENERATION-001 的 claim 侧语义：

1. enqueue 把期望代次落进 ``agent_execution_requests.execution_generation``；
2. claim 事务在 advisory 锁梯（agent-ws → agent-worker → job-mutation）之后
   对请求行代次与 jobs 现值做 CAS——不等即按 mutation 侧
   ``_cancel_queued_sql`` 的同语义取消该请求（state='cancelled' + manifest
   trim）并跳过，不 promote、不写 node_runs/executor_leases；
3. 代次匹配的正常 promote 给 node_runs / executor_leases 落同一戳；
4. 批写入段按（code 优先按 job_id、agent 按 (ws_lock_key, job_id)）的稳定
   序进入 evaluate/promote——SAVEPOINT 不释放 advisory xact 锁，无排序的
   两个并发批会在 job-mutation 域成环。
"""

from __future__ import annotations

from typing import Any

from server.app.agent_broker import AgentExecutionRequest
from server.app.agent_broker.claim_batch import claim_batch
from server.app.agent_broker.claim_batch_tx import _lock_order_sorted
from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import (
    SCAN_ROUNDS,
    ScanState,
    WorkerView,
    fetch_candidates,
)
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection, write_transaction
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import broker as _make_broker
from tests.helpers.agent_worker_api import insert_job_rows
from tests.postgres_support import TEST_DATABASE_URL

_WORKER_ID = "worker-gen"


def _register_worker() -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        capabilities=["generate"],
        max_concurrency=10,
        max_code_concurrency=5,
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def _seed_generation_request(job_db, *, job_id: str, generation: int) -> str:
    """同 tests.helpers.agent_worker_api.seed_request 的播种，但显式携带代次。"""
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    catalog = {"generator-v1": definition}
    replace_agent_catalog("test-workspace", catalog)
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id="test-workspace",
        agent_id="generator-v1",
    )
    execution_id = _make_broker(job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
            execution_generation=generation,
        )
    )
    assert execution_id is not None
    return execution_id


def _bump_generation(job_id: str) -> None:
    """只 bump jobs 代次而不取消 queued 请求——claim 侧 CAS 正是这个缺口的兜底。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            (job_id,),
        )


def _request_row(execution_id: str) -> dict[str, Any]:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select state, execution_generation, manifest_json"
            " from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_enqueue_persists_execution_generation(job_db) -> None:
    """enqueue 把 AgentExecutionRequest.execution_generation 落进请求行列。"""
    execution_id = _seed_generation_request(job_db, job_id="gen-enqueue", generation=3)

    assert int(_request_row(execution_id)["execution_generation"]) == 3


def test_claim_cancels_stale_generation_request(job_db) -> None:
    """代次过期（jobs 已 bump）的候选被 cancel_request 且不 promote。"""
    execution_id = _seed_generation_request(job_db, job_id="gen-stale", generation=0)
    _bump_generation("gen-stale")
    _register_worker()

    claimed = _make_broker(job_db.jobs_dir.parent).claim(_WORKER_ID)

    assert claimed is None
    row = _request_row(execution_id)
    assert row["state"] == "cancelled"
    # 与 mutation 侧 _cancel_queued_sql 同语义：终态同事务 trim manifest。
    assert '"trimmed": true' in str(row["manifest_json"])
    with read_connection(TEST_DATABASE_URL) as conn:
        runs = conn.execute(
            "select count(*) as cnt from node_runs where job_id='gen-stale'"
        ).fetchone()
        leases = conn.execute(
            "select count(*) as cnt from executor_leases where job_id='gen-stale'"
        ).fetchone()
        node = conn.execute(
            "select status from job_nodes where job_id='gen-stale' and node_key='generate'"
        ).fetchone()
    assert int(runs["cnt"]) == 0
    assert int(leases["cnt"]) == 0
    assert node["status"] == "pending"


def test_claim_promote_stamps_generation(job_db) -> None:
    """代次匹配的正常 claim：node_runs / executor_leases 落请求行代次。"""
    execution_id = _seed_generation_request(job_db, job_id="gen-match", generation=2)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=2 where id='gen-match'",
        )
    _register_worker()

    claimed = _make_broker(job_db.jobs_dir.parent).claim(_WORKER_ID)

    assert claimed is not None
    assert claimed.execution_id == execution_id
    assert claimed.execution_generation == 2
    with read_connection(TEST_DATABASE_URL) as conn:
        run = conn.execute(
            "select execution_generation from node_runs where job_id='gen-match'"
        ).fetchone()
        lease = conn.execute(
            "select execution_generation from executor_leases where job_id='gen-match'"
        ).fetchone()
    assert int(run["execution_generation"]) == 2
    assert int(lease["execution_generation"]) == 2


def test_claim_transaction_holds_job_mutation_lock(job_db) -> None:
    """claim 事务在 promote 前持有 hashtext('job-mutation:' || job_id) 的 xact 锁。"""
    _seed_generation_request(job_db, job_id="gen-lock", generation=0)
    _register_worker()
    broker = _make_broker(job_db.jobs_dir.parent)

    with write_transaction(TEST_DATABASE_URL) as conn:
        selected = fetch_candidates(
            conn, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="agent"
        )[0]
        claimed = evaluate_candidate(broker, conn, _WORKER_ID, selected, _agent_view(), ScanState())
        assert claimed is not None
        # 64 位无符号还原（比照 tests/db/test_execution_generation_schema.py
        # 的观测手法）——hashtext 可能为负，不能按 classid=0 过滤。
        held_rows = conn.execute(
            "select classid, objid from pg_locks"
            " where locktype='advisory' and objsubid=1 and pid=pg_backend_pid()"
        ).fetchall()
        held = {(int(row["classid"]) << 32) | int(row["objid"]) for row in held_rows}
        expected = conn.execute("select hashtext('job-mutation:gen-lock') as k").fetchone()["k"]
    assert int(expected) & 0xFFFFFFFFFFFFFFFF in held


def test_batch_claim_cancels_only_stale_candidates(job_db) -> None:
    """批路径：同批内过期代次候选被取消，新鲜候选正常 promote。"""
    stale_id = _seed_generation_request(job_db, job_id="gen-batch-stale", generation=0)
    fresh_id = _seed_generation_request(job_db, job_id="gen-batch-fresh", generation=0)
    _bump_generation("gen-batch-stale")
    _register_worker()

    claims = claim_batch(_make_broker(job_db.jobs_dir.parent), _WORKER_ID, None, None, limit=5)

    assert [claim.execution_id for claim in claims] == [fresh_id]
    assert _request_row(stale_id)["state"] == "cancelled"
    assert _request_row(fresh_id)["state"] == "claimed"


def test_lock_order_sorted_places_code_first_then_agent_ws_job() -> None:
    """批写入段排序：code 候选（不取 agent-ws 锁）按 job_id 在前，agent 候选
    按 (ws_lock_key, job_id) 升序——SAVEPOINT 不释放 advisory xact 锁，两个
    并发批若按不同顺序取 job-mutation 锁即成环。"""
    candidates = (
        {"kind": "agent", "ws_lock_key": 20, "job_id": "job-b"},
        {"kind": "code", "ws_lock_key": 99, "job_id": "job-z"},
        {"kind": "agent", "ws_lock_key": 10, "job_id": "job-y"},
        {"kind": "agent", "ws_lock_key": 10, "job_id": "job-x"},
        {"kind": "code", "ws_lock_key": 5, "job_id": "job-a"},
    )

    ordered = _lock_order_sorted(candidates)

    assert [(row["kind"], row["job_id"]) for row in ordered] == [
        ("code", "job-a"),
        ("code", "job-z"),
        ("agent", "job-x"),
        ("agent", "job-y"),
        ("agent", "job-b"),
    ]


def _agent_view() -> WorkerView:
    """单 kind 视图：只开 agent 池。"""
    return WorkerView(
        runtimes={"pi"},
        models={("*", "*", "*")},
        labels={"arch": "arm64"},
        allowed_workspaces=set(),
        agent_capacity=10,
        agent_active=0,
        code_capacity=0,
        code_active=0,
        protocol_version=2,
    )
