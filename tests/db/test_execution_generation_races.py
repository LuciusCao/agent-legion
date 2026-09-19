"""EXEC-GENERATION-001 的确定性交错测试矩阵（issue #759 阶段 7）。

手法比照 tests/db/test_status_counts_deadlock.py：两条连接，A 连接在主线程
手工控事务（stmt1 → 起 B 线程 → 等 B 卡在 job-mutation advisory 锁上
（pg_locks 观测，非裸 sleep）→ stmt2/commit），B 在 threading.Thread 里跑
完整协议操作；双方连接经 TIMED_DATABASE_URL 带 deadlock_timeout=50ms +
lock_timeout=5s（有环必现、意外等待有界），thread.join(timeout=30) 后断言
线程已死防假绿。每案断言「双方合理收尾 + 最终状态 == 某种合法串行序的
结果」。

矩阵（每案 docstring 标明钉住的协议规则）：

1. claim-before-guard：claim 先持 job-mutation 锁完成 promote，upgrade 的
   lease_guarded_mutation 阻塞到 claim 提交后看到 active lease →
   JobMutationConflict(busy)。
2. claim-after-guard：mutation 先持锁 bump 代次，claim 阻塞后 CAS 不符 →
   取消请求（cancelled + manifest trim），不 promote。
3. late enqueue：upgrade 提交后按旧代次 enqueue+claim → claim CAS 取消。
4. late local claim：code 池 LeaseClaimRequest 旧代次 → None，零写入。
5. late approval：旧代次 park 的 gate 在 upgrade 重置后 → approve_gate_atomic
   ApprovalGateConflict，job_nodes 不被翻转。
6. late failure：fail_without_lease 旧代次 → 跳过，节点保持 pending。
7. late finish：旧代次 lease 的 finish 在 upgrade 后到达 → lease released +
   node_run 终态落库，job_nodes/jobs 不被翻转。
8. 批 AB-BA 回归（阶段 7 任务 A）：finish_many 与 agent claim 批跨两个
   workspace 反向 job 序并发——统一 (ws 锁键, job_id) 批序下无 40P01、双方
   提交；旧纯 job_id 序下本案必死锁（突变自检覆盖）。

xdist 兼容：所有同步都走 pg_locks 观测 + join 超时，不用跨用例状态；每个
用例的 job/workspace id 独立，TRUNCATE 隔离照常。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import psycopg

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
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
from server.app.db.connection import connect_database
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_finish_batch import finish_many
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import lease_guarded_mutation, mark_nodes_for_rerun
from server.app.jobs.job_state_mutations import JobMutationConflict
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import insert_job_rows
from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

# 与 test_status_counts_deadlock.py 同款纪律：50ms 让重引入的环在毫秒级现形，
# 5s 给所有意外等待兜底（协议正确时锁等待 = A 的提交流程，远低于此）。
_separator = "&" if "?" in BASE_DATABASE_URL else "?"
TIMED_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{_separator}options="
    f"{quote(f'-csearch_path={TEST_SCHEMA} -cdeadlock_timeout=50ms -clock_timeout=5s', safe='')}"
)

_DEFINITION = AgentDefinition(
    capability="generate",
    runtime="pi",
    skill="question/generate",
    requires_labels={"arch": "arm64"},
)


def _agent_view() -> WorkerView:
    """单 kind 视图：只开 agent 池（同 claim generation 测试）。"""
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


def _register_worker(worker_id: str) -> None:
    AgentWorkerRegistry(TIMED_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        capabilities=["generate"],
        max_concurrency=10,
        max_code_concurrency=5,
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def _seed_agent_lane(job_db, *, workspace_id: str, job_id: str, node_key: str = "generate") -> None:
    """catalog + workspace/job/node/route/capacity，但尚不入队请求。"""
    replace_agent_catalog(workspace_id, {"generator-v1": _DEFINITION})
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key=node_key,
        limit=20,
        workspace_id=workspace_id,
        agent_id="generator-v1",
    )


def _enqueue(
    job_db, *, workspace_id: str, job_id: str, node_key: str = "generate", generation: int = 0
) -> str:
    execution_id = AgentExecutionBroker(
        TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent
    ).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id="generator-v1",
            agent_definition_hash=_DEFINITION.definition_hash(),
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


def _add_node(job_db, job_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


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


def _repo(job_db) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """B 侧线程：跑完整协议操作，结果/异常都收进 outcome（冲突也是数据）。"""
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


def _await_job_mutation_waiter(job_id: str, timeout: float = 10.0) -> None:
    """确定性同步点：等到有 backend 正等待该 job 的 job-mutation advisory 锁。

    pg_locks 里单键 advisory 锁（objsubid=1）拆成 (classid, objid) 两段 32 位，
    按无符号 64 位还原后与 hashtext 比对（比照 test_execution_generation_schema
    的 _held_advisory_keys 手法）。
    """
    with psycopg.connect(TIMED_DATABASE_URL, autocommit=True) as probe:
        row = probe.execute("select hashtext(%s)", (f"job-mutation:{job_id}",)).fetchone()
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
    raise AssertionError(f"no backend waited on job-mutation:{job_id} within {timeout}s")


def _upgrade_mutation(conn, job_id: str, node_keys: list[str]) -> None:
    """真实 upgrade mutation（clean 模式）：bump 代次并删除重建节点为 pending。"""
    upgrade_job_workflow_inherit(
        conn,
        job_id,
        workflow_revision_id="rev-race",
        workflow_version=2,
        workflow_definition_hash="hash-race",
        workflow_definition_snapshot_json="{}",
        node_keys=node_keys,
    )


def _run_upgrade(job_id: str, node_keys: list[str]) -> None:
    """B 侧完整 upgrade：lease_guarded_mutation（含 busy 检查）+ mutation。"""
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        _upgrade_mutation(conn, job_id, node_keys)


def _fetchone(sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with read_connection(TIMED_DATABASE_URL) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return dict(row)


def _node_row(job_id: str, node_key: str) -> dict[str, Any]:
    return _fetchone(
        "select status, execution_generation from job_nodes where job_id=%s and node_key=%s",
        (job_id, node_key),
    )


def _count(sql: str, params: tuple[Any, ...]) -> int:
    with read_connection(TIMED_DATABASE_URL) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row["cnt"])


# ---------------------------------------------------------------------------
# 1. claim-before-guard
# ---------------------------------------------------------------------------


def test_claim_before_guard_blocks_upgrade_until_commit_then_busy(job_db) -> None:
    """钉住「guard 与 claim 在同一 job-mutation 锁上互斥」：claim 先持锁完成
    promote（未提交），upgrade 的 lease_guarded_mutation 必须等到 claim 提交，
    随后看到 active lease → JobMutationConflict(busy)；最终状态 == 串行序
    「claim 成功 → upgrade 被拒」（代次不动、节点 running、lease active）。

    突变自检：摘掉 guard 的 advisory 锁后 B 不再等待（同步点超时）且 upgrade
    会盖掉 claim 的现场（节点被重置、代次被 bump）——两条断言同时变红。
    """
    job_id = "race1-job"
    _seed_agent_lane(job_db, workspace_id="race1-ws", job_id=job_id)
    _enqueue(job_db, workspace_id="race1-ws", job_id=job_id, generation=0)
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    conn_a = connect_database(TIMED_DATABASE_URL)
    try:
        selected = fetch_candidates(
            conn_a, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="agent"
        )[0]
        claim = evaluate_candidate(
            broker, conn_a, "worker-race1", selected, _agent_view(), ScanState()
        )
        assert claim is not None  # A 已 promote、持 job-mutation 锁、未提交

        thread, outcome = _start(lambda: _run_upgrade(job_id, ["generate"]))
        _await_job_mutation_waiter(job_id)  # B 卡在 guard 的 advisory 锁上
        conn_a.commit()
    finally:
        conn_a.close()
    _join(thread)

    error = outcome.get("error")
    assert isinstance(error, JobMutationConflict) and error.reason_code == "busy"
    node = _node_row(job_id, "generate")
    assert node["status"] == "running"  # claim 的现场原样保留
    jobs = _fetchone("select execution_generation from jobs where id=%s", (job_id,))
    assert int(jobs["execution_generation"]) == 0  # upgrade 被拒，代次未动
    lease = _fetchone("select status from executor_leases where job_id=%s", (job_id,))
    assert lease["status"] == "active"
    request = _fetchone("select state from agent_execution_requests where job_id=%s", (job_id,))
    assert request["state"] == "claimed"


# ---------------------------------------------------------------------------
# 2. claim-after-guard
# ---------------------------------------------------------------------------


def test_claim_after_guard_loses_generation_cas(job_db) -> None:
    """钉住「claim 侧代次 CAS」：mutation 先持锁 bump 代次，被锁挡住的 claim
    在 mutation 提交后读到 jobs 现值 != 请求落戳代次 → 按 mutation 侧同语义
    取消请求（cancelled + manifest trim）、不 promote；节点保持 pending，
    无 node_run/lease 泄漏。最终状态 == 串行序「rerun → claim 被拒」。

    同节点的 queued 请求由 mutation 侧 _cancel_queued_sql 自行取消，claim CAS
    兜底的是重置闭包之外的节点上的请求——故本案例 mutation 用兄弟节点 rerun
    构造（generate 不在重置闭包内，其 queued 请求存活到 claim 复查）。
    """
    job_id = "race2-job"
    _seed_agent_lane(job_db, workspace_id="race2-ws", job_id=job_id)
    execution_id = _enqueue(job_db, workspace_id="race2-ws", job_id=job_id, generation=0)
    _add_node(job_db, job_id, "sibling")
    _register_worker("worker-race2")
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    mark_nodes_for_rerun(conn_a, job_id, ["sibling"], {"sibling": []})
    thread, outcome = _start(lambda: broker.claim("worker-race2"))
    _await_job_mutation_waiter(job_id)  # B 卡在 claim 的 job-mutation 锁上
    stack.close()  # 提交 mutation：代次 bump 到 1
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None  # claim 被 CAS 拒
    request = _fetchone(
        "select state, manifest_json from agent_execution_requests where execution_id=%s",
        (execution_id,),
    )
    assert request["state"] == "cancelled"
    assert '"trimmed": true' in str(request["manifest_json"])
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"  # 未被重置闭包覆盖，行原样保留
    jobs = _fetchone("select execution_generation from jobs where id=%s", (job_id,))
    assert int(jobs["execution_generation"]) == 1
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 0
    assert _count("select count(*) as cnt from executor_leases where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 3. late enqueue
# ---------------------------------------------------------------------------


def test_late_enqueue_with_stale_generation_is_cancelled_at_claim(job_db) -> None:
    """钉住「enqueue 携带的代次在 claim 处兜底」：upgrade 提交后才按旧代次
    入队的请求（评估缓存过期产物）在 claim CAS 处被取消（cancelled +
    manifest trim），job_nodes 不动（保持 upgrade 后的 pending 新戳）。
    最终状态 == 串行序「upgrade → 迟到入队 → claim 拒」。"""
    job_id = "race3-job"
    _seed_agent_lane(job_db, workspace_id="race3-ws", job_id=job_id)
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        _upgrade_mutation(conn, job_id, ["generate"])
    # upgrade 提交后才入队的旧代次请求（dispatch 评估发生在 bump 之前）。
    execution_id = _enqueue(job_db, workspace_id="race3-ws", job_id=job_id, generation=0)
    _register_worker("worker-race3")
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    claimed = broker.claim("worker-race3")

    assert claimed is None
    request = _fetchone(
        "select state, manifest_json from agent_execution_requests where execution_id=%s",
        (execution_id,),
    )
    assert request["state"] == "cancelled"
    assert '"trimmed": true' in str(request["manifest_json"])
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1  # upgrade 的新戳原样保留
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 0
    assert _count("select count(*) as cnt from executor_leases where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 4. late local claim（code 池）
# ---------------------------------------------------------------------------


def test_late_local_claim_with_stale_generation_is_refused(job_db) -> None:
    """钉住「code 池 claim 的 fail-closed CAS」：LeaseClaimRequest 携带旧代次、
    被 upgrade 的 guard 锁挡住，upgrade 提交后 claim_lease 读到代次不符 →
    返回 None，零写入（节点 pending 新戳、无 node_runs/executor_leases 行）。
    最终状态 == 串行序「upgrade → 本地 claim 被拒」。"""
    job_id = "race4-job"
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id="race4-ws",
        agent_id="generator-v1",
    )
    repo = _repo(job_db)
    request = _code_claim_request("race4-ws", job_id, "generate", generation=0)

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    _upgrade_mutation(conn_a, job_id, ["generate"])
    thread, outcome = _start(lambda: repo.try_claim(request))
    _await_job_mutation_waiter(job_id)  # B 过了 code-pool 锁，卡在 job-mutation 锁上
    stack.close()  # 提交 upgrade：代次 bump 到 1
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 0
    assert _count("select count(*) as cnt from executor_leases where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 5. late approval
# ---------------------------------------------------------------------------


def test_late_approval_after_upgrade_conflicts(job_db) -> None:
    """钉住「approval 决策的代次/状态闸门」：旧代次 park 的 gate 在 upgrade
    bump + 重置后，被 guard 锁挡住的 approve_gate_atomic 读到的是新代次的
    pending 行 → ApprovalGateConflict，job_nodes 不被翻转、不落决策行。
    最终状态 == 串行序「upgrade → 旧决策被拒」。"""
    job_id = "race5-job"
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id="race5-ws",
        agent_id="generator-v1",
    )
    repo = _repo(job_db)
    assert repo.park_awaiting_approval(job_id, "generate", execution_generation=0) is True
    queries = JobQueries(TIMED_DATABASE_URL, job_db.jobs_dir)
    decision = {
        "id": "d-race5",
        "job_id": job_id,
        "node_key": "generate",
        "verdict": "approved",
        "note": "",
        "rework_target": "",
        "decided_by": "user:u1",
    }

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    _upgrade_mutation(conn_a, job_id, ["generate"])
    thread, outcome = _start(lambda: queries.approve_gate_atomic(decision))
    _await_job_mutation_waiter(job_id)  # B 卡在决策路径的 job-mutation 锁上
    stack.close()
    _join(thread)

    assert isinstance(outcome.get("error"), ApprovalGateConflict)
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"  # upgrade 重置后的行不被翻转
    assert int(node["execution_generation"]) == 1
    assert _count("select count(*) as cnt from approval_decisions where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 6. late failure（fail_without_lease）
# ---------------------------------------------------------------------------


def test_late_config_failure_with_stale_generation_is_skipped(job_db) -> None:
    """钉住「fail_without_lease 的代次 CAS」：旧代次的配置失败请求被 upgrade
    的 guard 锁挡住，upgrade 提交后代次不符 → 整体跳过（不写合成 node_run、
    不翻转节点），节点保持 upgrade 后的 pending 等新代次重新评估。
    最终状态 == 串行序「upgrade → 迟到 fail 被跳过」。"""
    job_id = "race6-job"
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id="race6-ws",
        agent_id="generator-v1",
    )
    repo = _repo(job_db)
    request = ConfigurationFailureRequest(
        workspace_id="race6-ws",
        job_id=job_id,
        workflow_key="race6-ws",
        node_key="generate",
        capability="review_keywords",
        log_path=f"logs/{job_id}-generate.log",
        execution_generation=0,
    )

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    _upgrade_mutation(conn_a, job_id, ["generate"])
    thread, outcome = _start(lambda: repo.fail_without_lease(request, "boom"))
    _await_job_mutation_waiter(job_id)
    stack.close()
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None  # 旧代次整体跳过
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 7. late finish
# ---------------------------------------------------------------------------


def test_late_finish_after_upgrade_settles_lease_only(job_db) -> None:
    """钉住「finish_lease 的迟到收尾语义」：旧代次 lease 的 finish 被 upgrade
    的 guard 锁挡住，upgrade 提交后读到代次不符 → lease released + node_run
    终态落库照常，但跳过 job_nodes 翻转与 sync_job_status——节点保持 upgrade
    后的 pending 新戳、jobs 保持 queued。最终状态 == 串行序「upgrade → 迟到
    finish 只收尾历史行」。

    生产 mutation 全部 reject_running_nodes=True，本案例复现的是节点已被
    其他路径带离 running、lease 行仍在的窗口下 mutation 与迟到 finish 的
    交错（代次 CAS 正是这个窗口的兜底），故 guard 放松 running 检查并把
    lease 的 expires_at 拨到过去以过 busy 检查。
    """
    job_id = "race7-job"
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id="race7-ws",
        agent_id="generator-v1",
    )
    repo = _repo(job_db)
    claim = repo.try_claim(_code_claim_request("race7-ws", job_id, "generate", generation=0))
    assert claim is not None
    with write_transaction(TIMED_DATABASE_URL) as conn:
        conn.execute(
            "update executor_leases set expires_at='2000-01-01'::timestamp where id=%s",
            (claim.lease_id,),
        )

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=False
        )
    )
    _upgrade_mutation(conn_a, job_id, ["generate"])
    thread, outcome = _start(
        lambda: repo.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
    )
    _await_job_mutation_waiter(job_id)  # B 卡在 finish 的 job-mutation 锁上
    stack.close()  # 提交 upgrade：代次 bump 到 1
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is True  # 收尾照常
    lease = _fetchone("select status from executor_leases where id=%s", (claim.lease_id,))
    assert lease["status"] == "released"
    run = _fetchone(
        "select status from node_runs where job_id=%s and node_key='generate'", (job_id,)
    )
    assert run["status"] == "completed"  # 历史行终态落库
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1  # 新代次行不被翻转
    jobs = _fetchone("select status, execution_generation from jobs where id=%s", (job_id,))
    assert jobs["status"] == "queued"  # sync_job_status 被跳过
    assert int(jobs["execution_generation"]) == 1


# ---------------------------------------------------------------------------
# 8. 批 AB-BA 回归（阶段 7 任务 A 的验收）
# ---------------------------------------------------------------------------


def _inverted_ws_pair(job_db) -> tuple[str, str]:
    """一对文本序与 hashtext('agent-ws:' || …)::int 序相反的 workspace id。

    返回 (ws_first, ws_second)：ws_first 锁键更小（文本更大），ws_second 锁键
    更大（文本更小）——配反向 job id 后，纯 job_id 序与锁键序相反。
    """
    pool: list[tuple[str, int]] = []
    with job_db.connect() as conn:
        for i in range(200):
            wid = f"race8-ws-{i:03d}"
            row = conn.execute("select hashtext(%s)::int as k", (f"agent-ws:{wid}",)).fetchone()
            assert row is not None
            pool.append((wid, int(row["k"])))
    for wid, key in sorted(pool):
        smaller = [w for w, other_key in pool if w > wid and other_key < key]
        if smaller:
            return min(smaller), wid
    raise AssertionError("no inverted (text, lock-key) pair among 200 candidates")


def test_finish_many_vs_agent_claim_batch_no_ab_ba(job_db) -> None:
    """钉住「全库 job-mutation 批序唯一」（阶段 7 任务 A）：finish_many 与
    agent claim 批按同一 (ws 锁键, job_id) 序取 job-mutation 锁。

    两个 workspace（锁键序与 job_id 序相反）各两节点：n1 已被 code 池认领
    （running、lease active），n2 排队 agent 请求。A = 手工驱动的 agent 批
    （_lock_order_sorted 后先领锁键较小侧的 job-z），B = finish_many（两条
    n1 lease）。统一序下 B 的第一项就等 A 提交，双方干净提交、无 40P01；
    旧纯 job_id 序下 B 先取 job-a 的锁 → 与 A 成环必 40P01（deadlock_timeout
    =50ms，突变自检覆盖）。
    """
    ws_first, ws_second = _inverted_ws_pair(job_db)
    # job_id 序与锁键序相反：job-a 在锁键较大的 ws_second，job-z 在 ws_first。
    job_first, job_second = "race8-job-z", "race8-job-a"
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    repo = _repo(job_db)
    lease_by_job: dict[str, str] = {}
    for workspace_id, job_id in ((ws_first, job_first), (ws_second, job_second)):
        _seed_agent_lane(job_db, workspace_id=workspace_id, job_id=job_id, node_key="n2")
        _add_node(job_db, job_id, "n1")
        _enqueue(job_db, workspace_id=workspace_id, job_id=job_id, node_key="n2", generation=0)
        claim = repo.try_claim(_code_claim_request(workspace_id, job_id, "n1", generation=0))
        assert claim is not None
        lease_by_job[job_id] = claim.lease_id

    conn_a = connect_database(TIMED_DATABASE_URL)
    try:
        candidates = fetch_candidates(
            conn_a, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="agent"
        )
        ordered = _lock_order_sorted(tuple(candidates))
        assert [str(row["job_id"]) for row in ordered] == [job_first, job_second]
        claim1 = evaluate_candidate(
            broker, conn_a, "worker-race8", ordered[0], _agent_view(), ScanState()
        )
        assert claim1 is not None  # A 持 job-mutation:job-z，未提交

        result = ExecutionResult(status="completed", exit_code=0)
        thread, outcome = _start(
            lambda: finish_many(
                repo,
                # 入队序按 job_id 序——排序键必须纠正它（两版代码下 B 的第一项
                # 分别是 job-z（新）与 job-a（旧））。
                [(lease_by_job[job_second], result, None), (lease_by_job[job_first], result, None)],
            )
        )
        _await_job_mutation_waiter(job_first)  # B 卡在 job-z 的锁上
        claim2 = evaluate_candidate(
            broker, conn_a, "worker-race8", ordered[1], _agent_view(), ScanState()
        )
        assert claim2 is not None
        conn_a.commit()
    finally:
        conn_a.close()
    _join(thread)

    assert outcome.get("error") is None
    verdicts, _callbacks = outcome["result"]
    assert verdicts == [True, True]
    for job_id in (job_first, job_second):
        assert _node_row(job_id, "n1")["status"] == "completed"
        assert _node_row(job_id, "n2")["status"] == "running"
        lease = _fetchone("select status from executor_leases where id=%s", (lease_by_job[job_id],))
        assert lease["status"] == "released"
        request = _fetchone("select state from agent_execution_requests where job_id=%s", (job_id,))
        assert request["state"] == "claimed"
