"""EXEC-GENERATION-001：enqueue 代次 CAS 与混合批序的交错测试（#645 评审 P1/P2）。

手法比照 test_execution_generation_races.py /
test_sweeper_generation_races.py：TIMED_DATABASE_URL 带
deadlock_timeout=50ms + lock_timeout=5s，同步点走 pg_locks 观测（非裸
sleep），thread.join(timeout=30) 后断言线程已死防假绿。

矩阵：

1. 迟到 agent enqueue（P1）：mutation 持 job-mutation 锁 bump 代次期间，
   按旧代次打包的 enqueue 被锁挡住；mutation 提交后 CAS 不符 → 返回
   None、零行插入。调用方按既有「已有活动请求」跳过语义处理（节点保持
   pending，下一评估轮按新代次重派）。
2. 迟到 code enqueue（P1 原始场景）：同构但 kind='code'——若 stale
   queued 行被插入而远端 Worker 离线，claim CAS 永不发生，
   ``has_active_request`` 会把本可由本地 code 池执行的新代次节点无限期
   挡住。
3. 新代次对照：mutation 提交后按新代次 enqueue 正常插入（行为不变）。
4. 混合 claim 批 × finish_many（P2）：批内 code 候选与 agent 候选共用
   纯 (job 锁键, job_id) 全序（#1346：实际 advisory 锁键；#1378 codex P2：
   无 ws 分桶，agent-ws 池锁批首预取）→ 无 40P01、双方干净提交；旧
   「code 块按 job_id 排最前」序下本案必成环（突变自检覆盖）。
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
from server.app.db.connection import connect_database
from server.app.db.transaction import read_connection
from server.app.executors._lease_finish_batch import finish_many
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs.atomic_mutations import lease_guarded_mutation
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import insert_job_rows
from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

# 与 test_execution_generation_races.py 同款纪律：50ms 让重引入的环在毫秒级
# 现形，5s 给所有意外等待兜底。
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


def _mixed_view() -> WorkerView:
    """双池视图：agent 与 code 候选都要过准入。"""
    return WorkerView(
        runtimes={"pi"},
        models={("*", "*", "*")},
        labels={"arch": "arm64"},
        allowed_workspaces=set(),
        agent_capacity=10,
        agent_active=0,
        code_capacity=10,
        code_active=0,
        protocol_version=2,
    )


def _seed_agent_lane(job_db, *, workspace_id: str, job_id: str, node_key: str) -> None:
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


def _seed_code_lane(job_db, *, workspace_id: str, job_id: str, node_keys: list[str]) -> None:
    """kind='code' 的最小行集（无 Agent 路由——code 请求跳过路由校验）。"""
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
        for node_key in node_keys:
            conn.execute(
                "insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key)
            )


def _add_node(job_db, job_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _enqueue_agent(
    job_db, *, workspace_id: str, job_id: str, node_key: str, generation: int
) -> str | None:
    return AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent).enqueue(
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


def _enqueue_code(
    job_db, *, workspace_id: str, job_id: str, node_key: str, generation: int
) -> str | None:
    return AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            # kind='code' 行：agent_id 携带 capability、hash 携带 code hash。
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "config": {"mode": "fast"},
            },
            kind="code",
            execution_generation=generation,
        )
    )


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
    按无符号 64 位还原后与 hashtext 比对（同 test_execution_generation_races）。
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
# 1. 迟到 agent enqueue（P1）
# ---------------------------------------------------------------------------


def test_stale_agent_enqueue_is_refused_at_insert(job_db) -> None:
    """钉住「enqueue 在统一锁域内校验代次」（#645 评审 P1）：mutation 持
    job-mutation 锁 bump 代次期间，按旧代次打包的 enqueue 阻塞在锁上；
    mutation 提交后读到代次不符 → 返回 None、零行插入，节点保持 upgrade
    后的 pending 新戳。最终状态 == 串行序「upgrade → 迟到入队被拒」。

    突变自检：摘掉 enqueue 的 CAS 后 B 不再等待（同步点超时）且旧代次行
    被插入（返回 execution_id）——两条断言同时变红。"""
    job_id = "eq1-job"
    _seed_agent_lane(job_db, workspace_id="eq1-ws", job_id=job_id, node_key="generate")

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    _upgrade_mutation(conn_a, job_id, ["generate"])  # 代次 bump 到 1，未提交
    thread, outcome = _start(
        lambda: _enqueue_agent(
            job_db, workspace_id="eq1-ws", job_id=job_id, node_key="generate", generation=0
        )
    )
    try:
        _await_job_mutation_waiter(job_id)  # B 卡在 enqueue 的 job-mutation 锁上
        stack.close()  # 提交 upgrade
    finally:
        stack.close()
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None  # 代次不符：不插入
    assert (
        _count("select count(*) as cnt from agent_execution_requests where job_id=%s", (job_id,))
        == 0
    )
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1  # upgrade 的新戳原样保留


# ---------------------------------------------------------------------------
# 2. 迟到 code enqueue（P1 原始场景）
# ---------------------------------------------------------------------------


def test_stale_code_enqueue_is_refused_at_insert(job_db) -> None:
    """钉住「code enqueue 同样入域」（P1 原始场景）：异步 code enqueue 在代次
    N 打包输入、INSERT 前 upgrade 提交（N+1，_cancel_queued_sql 已取消当时
    存在的旧请求）。CAS 拒绝后零行插入——否则该 stale queued 行落在 claim
    CAS 取消面内、却在远端 Worker 离线时永远等不到 claim，
    ``has_active_request`` 会把新代次节点的本地 code 池重派无限期挡住。"""
    job_id = "eq2-job"
    _seed_code_lane(job_db, workspace_id="eq2-ws", job_id=job_id, node_keys=["package"])

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    _upgrade_mutation(conn_a, job_id, ["package"])
    thread, outcome = _start(
        lambda: _enqueue_code(
            job_db, workspace_id="eq2-ws", job_id=job_id, node_key="package", generation=0
        )
    )
    try:
        _await_job_mutation_waiter(job_id)
        stack.close()
    finally:
        stack.close()
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None
    assert (
        _count("select count(*) as cnt from agent_execution_requests where job_id=%s", (job_id,))
        == 0
    )
    node = _node_row(job_id, "package")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1


# ---------------------------------------------------------------------------
# 3. 新代次对照：mutation 后按新代次 enqueue 正常插入
# ---------------------------------------------------------------------------


def test_enqueue_with_current_generation_after_mutation_succeeds(job_db) -> None:
    """对照案：upgrade 提交（代次 1）后按新代次 enqueue 正常插入——CAS 只拒
    旧代次，不改变代次相符时的入队行为。"""
    job_id = "eq3-job"
    _seed_agent_lane(job_db, workspace_id="eq3-ws", job_id=job_id, node_key="generate")
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        _upgrade_mutation(conn, job_id, ["generate"])

    execution_id = _enqueue_agent(
        job_db, workspace_id="eq3-ws", job_id=job_id, node_key="generate", generation=1
    )

    assert execution_id is not None
    request = _fetchone(
        "select state, execution_generation from agent_execution_requests where execution_id=%s",
        (execution_id,),
    )
    assert request["state"] == "queued"
    assert int(request["execution_generation"]) == 1
    node = _node_row(job_id, "generate")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1


# ---------------------------------------------------------------------------
# 4. 混合 claim 批 × finish_many（P2）
# ---------------------------------------------------------------------------


def test_mixed_claim_batch_vs_finish_many_no_ab_ba(job_db) -> None:
    """钉住「混合批与 finish 批共用同一全序」（#645 评审 P2；#1346/#1378 改为
    纯 (job 锁键, job_id) 序）：claim 批的 code 候选与 agent 候选按同一把
    实际锁键排序（code 不取 agent-ws 锁；agent-ws 池锁由批首预取覆盖，
    #1378 codex P2）。

    两个 workspace 各两节点：n1 已被 code 池认领（running、lease active），
    n2 一个排 agent 请求、一个排 code 请求。A = 手工驱动的混合批
    （_lock_order_sorted 后先领锁键较小侧），B = finish_many（两条 n1
    lease，入队序相反以证明排序键纠正了它）。统一序下 B 的第一项就等 A
    提交，双方干净提交、无 40P01；任何异序（旧 code-first 块、ws 分桶、
    id 文本序）都可能让双方先抢不同的锁成环（deadlock_timeout=50ms，且
    排序断言立即变红——突变自检覆盖）。编舞不依赖 ws/id 文本序：
    job_first/job_second 按真实 job-mutation 锁键现算。
    """
    ws_agent, ws_code = "eq4-ws-agent", "eq4-ws-code"
    job_agent, job_code = "eq4-job-agent", "eq4-job-code"
    with job_db.connect() as conn:
        keys = {
            job: int(
                conn.execute("select hashtext('job-mutation:' || %s)::int as k", (job,)).fetchone()[
                    "k"
                ]
            )
            for job in (job_agent, job_code)
        }
    job_first, job_second = sorted(keys, key=lambda j: (keys[j], j))
    kind_by_job = {job_agent: "agent", job_code: "code"}
    ws_by_job = {job_agent: ws_agent, job_code: ws_code}
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    repo = _repo(job_db)
    # agent 侧 job：agent 请求（n2）+ n1 的 code 池 lease。
    _seed_agent_lane(job_db, workspace_id=ws_agent, job_id=job_agent, node_key="n2")
    _add_node(job_db, job_agent, "n1")
    assert (
        _enqueue_agent(job_db, workspace_id=ws_agent, job_id=job_agent, node_key="n2", generation=0)
        is not None
    )
    # code 侧 job：code 请求（n2）+ n1 的 code 池 lease。
    _seed_code_lane(job_db, workspace_id=ws_code, job_id=job_code, node_keys=["n1", "n2"])
    assert (
        _enqueue_code(job_db, workspace_id=ws_code, job_id=job_code, node_key="n2", generation=0)
        is not None
    )
    lease_by_job: dict[str, str] = {}
    for job_id in (job_agent, job_code):
        claim = repo.try_claim(_code_claim_request(ws_by_job[job_id], job_id, "n1", generation=0))
        assert claim is not None
        lease_by_job[job_id] = claim.lease_id

    conn_a = connect_database(TIMED_DATABASE_URL)
    try:
        candidates = tuple(
            fetch_candidates(
                conn_a, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="agent"
            )
        ) + tuple(
            fetch_candidates(
                conn_a, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="code"
            )
        )
        ordered = _lock_order_sorted(candidates)
        # 统一纯序：锁键较小者在前（与 kind/ws/id 文本无关）——本断言即 P2
        # 的排序钉子。
        assert [str(row["job_id"]) for row in ordered] == [job_first, job_second]
        claim1 = evaluate_candidate(
            broker, conn_a, "worker-eq4", ordered[0], _mixed_view(), ScanState()
        )
        assert claim1 is not None and claim1.kind == kind_by_job[job_first]
        # A 持 job-mutation:job_first

        result = ExecutionResult(status="completed", exit_code=0)
        thread, outcome = _start(
            lambda: finish_many(
                repo,
                # 入队序与锁键序相反——排序键必须纠正它。
                [(lease_by_job[job_second], result, None), (lease_by_job[job_first], result, None)],
            )
        )
        _await_job_mutation_waiter(job_first)  # B 卡在 job_first 的锁上
        claim2 = evaluate_candidate(
            broker, conn_a, "worker-eq4", ordered[1], _mixed_view(), ScanState()
        )
        assert claim2 is not None and claim2.kind == kind_by_job[job_second]
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
