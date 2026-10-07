"""claim_evaluate 的取消分支回归（#955，#917 P2「4 个取消分支 0 引用」）。

``evaluate_candidate`` 在锁梯之后、promote 之前有一组「候选已失效 → 取消请求
并跳过」的分支。它们由 ``claim_windows`` / ``claim_batch_tx`` 静态调用（非反射），
是选择 → 写入之间并发变更的兜底，不是死代码；此前只是没有用例覆盖。本文件以
「先选出候选、再在同一事务内模拟并发变更、最后 evaluate」的方式钉住：

- ``job_terminal``：选择之后 job 已进入终态 → 取消请求，不 promote；
- ``node_not_pending``：选择之后节点已不在 pending/ready/stale → 取消请求，
  不写 node_runs / executor_leases。

``job_missing`` 分支在当前 schema 下不可达（``agent_execution_requests.job_id``
外键 ``on delete cascade``，且请求行已被本事务 ``for update`` 锁住，删 job 的
级联必须等本事务结束），作为 schema 漂移时的防御保留，此处不构造。
"""

from __future__ import annotations

from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import (
    SCAN_ROUNDS,
    ScanState,
    WorkerView,
    fetch_candidates,
)
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection, write_transaction
from tests.helpers.agent_worker_api import broker as _make_broker
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL

_WORKER_ID = "worker-cancel-branches"


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


def _agent_view() -> WorkerView:
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


def _evaluate_after(job_db, job_id: str, mutate_sql: str) -> tuple[object, ScanState]:
    """选出候选后在同一事务内执行 ``mutate_sql``（模拟选择 → 写入间的并发变更），再 evaluate。"""
    seed_request(job_db, job_id=job_id)
    _register_worker()
    state = ScanState()
    with write_transaction(TEST_DATABASE_URL) as conn:
        candidates = fetch_candidates(
            conn, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="agent"
        )
        selected = next(row for row in candidates if row["job_id"] == job_id)
        conn.execute(mutate_sql, (job_id,))
        claimed = evaluate_candidate(
            _make_broker(job_db.jobs_dir.parent), conn, _WORKER_ID, selected, _agent_view(), state
        )
    return claimed, state


def _request_state(job_id: str) -> str:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return str(row["state"])


def _promote_rows(job_id: str) -> tuple[int, int]:
    with read_connection(TEST_DATABASE_URL) as conn:
        runs = conn.execute(
            "select count(*) as cnt from node_runs where job_id=%s", (job_id,)
        ).fetchone()
        leases = conn.execute(
            "select count(*) as cnt from executor_leases where job_id=%s", (job_id,)
        ).fetchone()
    return int(runs["cnt"]), int(leases["cnt"])


def test_terminal_job_after_selection_cancels_request(job_db) -> None:
    claimed, state = _evaluate_after(
        job_db, "cancel-terminal", "update jobs set status='failed' where id=%s"
    )

    assert claimed is None
    assert state.skip_reasons["job_terminal"] == 1
    assert _request_state("cancel-terminal") == "cancelled"
    assert _promote_rows("cancel-terminal") == (0, 0)


def test_node_left_pending_after_selection_cancels_request(job_db) -> None:
    claimed, state = _evaluate_after(
        job_db,
        "cancel-node",
        "update job_nodes set status='completed' where job_id=%s and node_key='generate'",
    )

    assert claimed is None
    assert state.skip_reasons["node_not_pending"] == 1
    assert _request_state("cancel-node") == "cancelled"
    assert _promote_rows("cancel-node") == (0, 0)
