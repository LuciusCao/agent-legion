"""远程 code claim 的节点级并发限制（issue #1149）。

修复前 workspace_node_limits 只在本地 code 池路径生效
（executors/_lease_claim_limits.check_claim_capacity）；远程路径
（agent_broker/claim_evaluate.evaluate_candidate 的 code 分支）没有任何
(workspace_id, node_key) 检查，同一节点被跨 job 并行 claim。本文件钉住
修复的各个面：

1. 跨 job 同节点：limit=1 时第二个请求 skip（node_limit_full）留队列，
   第一个完成后（finish + mark_done 收尾 lease）第三轮 claim 放行；
2. 本地/远程混合计数：本地池 lease（executor_id='code'）占位时远程
   claim 被拒——计数只并 code 形态租约（'code' + 'agent:code:%'，
   #1167），同一张 executor_leases 表合并；
3. 批 claim（#546/#555）同节点多候选：批写阶段重跑 evaluate，批内
   第二个候选 skip，第一个 claim 保留（savepoint 语义不受影响）；
4. 对抗评审 P2-1（claim 侧）：probe 判「无 limit 行、不取锁」后、检查前
   另一会话插入 limit 并提交——claim 不得无锁计数（node_limit_appeared
   skip 留队列），下一轮以正常锁序领取；
5. 对抗评审 P2-1（写侧）：replace_workspace_node_limits 先取 code-pool
   锁再写任何 limit 行——锁被他人持有时配置写阻塞（pg_locks 观测）；
6. 对抗评审 P2-2：批写事务在首个 job-mutation 锁之前统一取 code-pool
   （agent 候选在前、带 limit 的 code 候选在后的批序下，逐候选获取会
   倒置全序）；
7. shard 候选：检查位于 shard 分支之前，limit 满时 skip 不触发
   try_start_shard 副作用；
8. limit 运行时可变：现值 claim 时现读（2→1 按新值 skip、回调后放行；
   请求行上的 enqueue 时 audit 值从不被强制）——与本地路径契约式校验
   有意不同的核心语义；
9. #1167 P2：同 node_key 的 agent:<id> 租约（旧 revision 的 agent job
   在跑）不占 code 节点额度——limit=1 且 agent 租约占位时远程 code
   claim 仍放行；修前计数不筛执行类型，一个旧 agent 执行就能阻塞
   所有新 code claim；
10. #1167 计数口径表矩阵：{本地 code lease, 远程 code lease, agent
    lease} × limit=1 三种占位形态各自断言——前两种（code 形态）拒，
    agent 形态放行；占位租约的 executor_id 形态本身入断言（口径表
    的行维度）。

串行搭建（用例 1-3、7、8 无交错线程）：每轮 claim 是独立提交的事务，
计数在提交后对下一轮可见，无需 pg_locks 同步点；用例 4-6 的交错用
monkeypatch/线程 + pg_locks 观测构造确定性同步点（比照
tests/db/test_execution_generation_races.py 的纪律）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest, claim_node_limit
from server.app.agent_broker.claim_batch import claim_batch_with_retry
from server.app.agent_broker.claim_batch_select import select_batch_candidates
from server.app.agent_broker.claim_batch_tx import claim_batch_in_transaction
from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import SCAN_ROUNDS, ScanState, WorkerView, fetch_candidates
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import CODE_EXECUTOR_ID, ExecutionResult, LeaseClaimRequest
from server.app.jobs.node_limits import replace_workspace_node_limits
from shared.protocol import PROTOCOL_VERSION
from tests.helpers.agent_worker_api import seed_request
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
    job_db, workspace_id: str, node_key: str, *, limit: int | None, job_ids: list[str]
) -> None:
    """workspace + jobs + node 行 + 节点 limit（limit 行有 FK 到 workspaces）。

    ``limit=None`` 只搭 job 面、不插 limit 行（P2-1 首配竞态用例的起点）。"""
    for job_id in job_ids:
        _seed_code_job(job_db, workspace_id, job_id, node_key)
    if limit is not None:
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


def _enqueue_code(
    job_db,
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    order: int = 0,
    shard_index: int | None = None,
) -> str:
    manifest: dict[str, Any] = {
        "kind": "code",
        "capability": "package",
        "code_hash": "abc123",
        "job_id": job_id,
        "log_path": f"logs/{job_id}-{node_key}.log",
        "config": {"mode": "fast"},
    }
    if shard_index is not None:
        manifest["shard_index"] = shard_index
    execution_id = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key=workspace_id,
            node_key=node_key,
            agent_id="package",
            agent_definition_hash="codehash",
            manifest=manifest,
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
    """用例 2：本地/远程合并计数——本地 code 池 lease（executor_id='code'）
    占位时，远程 claim 被同一 (workspace, node) 计数拒绝（单条 claim 路径，
    请求留队列）；计数只并 code 形态租约（#1167，'code' + 'agent:code:%'），
    非 code 形态的排除见用例 9。"""
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


# ---------------------------------------------------------------------------
# 对抗评审 P2-1/P2-2：锁域收口的确定性交错用例
# ---------------------------------------------------------------------------


def _code_view() -> WorkerView:
    """直连 evaluate_candidate 的 code-kind 视图（同
    test_execution_generation_races._agent_view 的手法）。"""
    return WorkerView(
        runtimes=set(),
        models=set(),
        labels={},
        allowed_workspaces=set(),
        agent_capacity=0,
        agent_active=0,
        code_capacity=10,
        code_active=0,
        protocol_version=PROTOCOL_VERSION,
    )


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """B 侧线程：结果/异常都收进 outcome（比照 test_execution_generation_races）。"""
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
    assert not thread.is_alive(), "write-side transaction never resolved"


def _await_code_pool_waiter(timeout: float = 10.0) -> None:
    """确定性同步点：等到有 backend 正等待 code-pool advisory 锁（比照
    test_execution_generation_races._await_job_mutation_waiter 的手法：
    pg_locks 观测，非裸 sleep）。"""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as probe:
        row = probe.execute("select hashtext(%s)", ("code-pool",)).fetchone()
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
    raise AssertionError(f"no backend waited on code-pool within {timeout}s")


def test_first_config_insert_mid_claim_skips_unlocked_count(job_db, monkeypatch) -> None:
    """P2-1（claim 侧）：probe 判「无 limit 行、不取锁」后、节点检查前，
    配置插入在另一会话提交——claim 不得在无锁状态下计数新行（与并发持锁
    claimant 竞态可超限 admit），按 node_limit_appeared skip 留队列；下一轮
    probe 命中 → 取锁 → 正常 enforce → admit（skip 不产生 livelock）。"""
    workspace_id, node_key = "ws-1149-p21c", "package"
    _seed_code_lane(job_db, workspace_id, node_key, limit=None, job_ids=["job-p21c"])
    execution_id = _enqueue_code(job_db, workspace_id, "job-p21c", node_key)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    inserted = {"done": False}
    real_enter = claim_node_limit.enter_code_pool_domain

    def enter_with_mid_claim_insert(conn, selected, kind, batch_code_pool_lock=None):
        held = real_enter(conn, selected, kind, batch_code_pool_lock)
        if kind == "code" and not held and not inserted["done"]:
            inserted["done"] = True
            # 竞态另一半：probe（无行、未取锁）与检查之间，另一会话插入并提交。
            with job_db.connect() as writer:
                writer.execute(
                    "insert into workspace_node_limits(workspace_id, node_key, concurrency_limit)"
                    " values (%s, %s, 1)",
                    (workspace_id, node_key),
                )
        return held

    monkeypatch.setattr(claim_node_limit, "enter_code_pool_domain", enter_with_mid_claim_insert)

    state = ScanState()
    with write_transaction(TEST_DATABASE_URL) as conn:
        selected = fetch_candidates(
            conn, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="code"
        )[0]
        claim = evaluate_candidate(broker, conn, "worker-p21c", selected, _code_view(), state)

    assert claim is None
    assert state.skip_reasons["node_limit_appeared"] == 1
    assert _request_state(job_db, execution_id) == "queued"
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 0

    state2 = ScanState()
    with write_transaction(TEST_DATABASE_URL) as conn:
        selected = fetch_candidates(
            conn, per_workspace=SCAN_ROUNDS[0][0], window=SCAN_ROUNDS[0][1], kind="code"
        )[0]
        claim2 = evaluate_candidate(broker, conn, "worker-p21c", selected, _code_view(), state2)

    assert claim2 is not None
    assert _request_state(job_db, execution_id) == "claimed"
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 1


def _write_limit_rows(workspace_id: str, node_key: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        replace_workspace_node_limits(
            conn, workspace_id, [{"node_key": node_key, "concurrency_limit": 1}]
        )


def test_node_limit_config_write_takes_code_pool_lock_first(job_db) -> None:
    """P2-1（写侧）：replace_workspace_node_limits 在写任何 limit 行之前取
    code-pool 锁——锁被他人持有时配置写阻塞（与持锁 claim 串行化，持锁
    claim 事务内的 limit 现值因此稳定）。去掉该锁时本用例的同步点超时
    变红（写不阻塞、直接完成）。"""
    workspace_id, node_key = "ws-1149-p21w", "package"
    _seed_code_job(job_db, workspace_id, "job-seed", node_key)

    holder = psycopg.connect(TEST_DATABASE_URL)
    try:
        holder.execute("select pg_advisory_xact_lock(hashtext('code-pool'))")
        thread, outcome = _start(lambda: _write_limit_rows(workspace_id, node_key))
        _await_code_pool_waiter()
        holder.commit()
    finally:
        holder.close()
    _join(thread)

    assert outcome.get("error") is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select concurrency_limit from workspace_node_limits"
            " where workspace_id=%s and node_key=%s",
            (workspace_id, node_key),
        ).fetchone()
    assert row is not None
    assert int(row["concurrency_limit"]) == 1


def test_batch_takes_code_pool_lock_before_any_job_mutation(job_db) -> None:
    """P2-2：批写事务在首个候选取 job-mutation 锁之前统一取 code-pool——
    批序里 agent 候选（不取 code-pool）在前、带 limit 行的 code 候选在后
    时，逐候选获取会把 code-pool 排到 job-mutation 之后（倒置全局锁序、
    与「持 code-pool 等 job-mutation」的本地 claim 成环）；集中 probe 在
    事务首句恢复全序。spy 记录全部 advisory 锁键的到达次序作断言。"""
    workspace_id, node_key = "ws-1149-p22", "package"
    seed_request(job_db, job_id="job-a-agent", workspace_id=workspace_id)
    _seed_code_job(job_db, workspace_id, "job-z-code", node_key)
    _set_node_limit(job_db, workspace_id, node_key, 1)
    _enqueue_code(job_db, workspace_id, "job-z-code", node_key)
    _register_code_worker("worker-1149-p22")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    selection = select_batch_candidates(pool, "worker-1149-p22", None, None, limit=2)
    assert {str(row["kind"]) for row in selection.candidates} == {"agent", "code"}

    advisory_keys: list[str] = []
    with write_transaction(TEST_DATABASE_URL) as conn:
        real_execute = conn.execute

        def spy(sql: Any, params: Any = None) -> Any:
            cursor = real_execute(sql, params)
            if "pg_advisory_xact_lock" in str(sql) and params:
                advisory_keys.append(str(params[0]))
            return cursor

        conn.execute = spy  # type: ignore[method-assign]
        outcome = claim_batch_in_transaction(
            pool, conn, "worker-1149-p22", None, None, selection=selection
        )

    assert [claim.job_id for claim in outcome.claims] == ["job-a-agent", "job-z-code"]
    assert "code-pool" in advisory_keys
    first_job_mutation = next(
        (i for i, key in enumerate(advisory_keys) if key.startswith("job-mutation:")), None
    )
    assert first_job_mutation is not None
    assert advisory_keys.index("code-pool") < first_job_mutation


# ---------------------------------------------------------------------------
# P3-2：shard 候选与 limit 运行时可变
# ---------------------------------------------------------------------------


def test_shard_candidate_skips_on_node_limit_without_shard_side_effects(job_db) -> None:
    """shard 候选：节点级检查位于 shard 分支（try_start_shard）之前——
    limit 满时带 shard_index 的 code 请求 skip 留队列，node_shards 不绑定
    execution_id、job_nodes 不翻 running、零 node_run 写入。"""
    workspace_id, node_key = "ws-1149-shard", "package"
    _seed_code_lane(job_db, workspace_id, node_key, limit=1, job_ids=["job-shard", "job-shard-occ"])
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    # 名额占用：本地 claim（job-shard-occ，无 shard）。
    assert repo.try_claim(_local_claim_request(workspace_id, "job-shard-occ", node_key, 1))
    # shard 候选：node_shards 行待绑定 + manifest 带 shard_index。
    with job_db.connect() as conn:
        conn.execute(
            "insert into node_shards(job_id, node_key, shard_index, status)"
            " values (%s, %s, 0, 'pending')",
            ("job-shard", node_key),
        )
    execution_id = _enqueue_code(job_db, workspace_id, "job-shard", node_key, shard_index=0)
    _register_code_worker("worker-1149-shard")

    claim = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent).claim(
        "worker-1149-shard"
    )

    assert claim is None  # node_limit_full：检查在 shard 分支之前
    assert _request_state(job_db, execution_id) == "queued"
    with job_db._connect_read() as conn:
        shard = conn.execute(
            "select status, execution_id from node_shards"
            " where job_id=%s and node_key=%s and shard_index=0",
            ("job-shard", node_key),
        ).fetchone()
        node = conn.execute(
            "select status from job_nodes where job_id=%s and node_key=%s",
            ("job-shard", node_key),
        ).fetchone()
        runs = conn.execute(
            "select count(*) as c from node_runs where job_id=%s", ("job-shard",)
        ).fetchone()
    assert shard is not None
    assert shard["status"] == "pending"  # try_start_shard 未发生：未绑定
    assert str(shard["execution_id"]) == ""
    assert node is not None and node["status"] == "pending"
    assert int(runs["c"]) == 0


def test_node_limit_is_read_fresh_per_claim(job_db) -> None:
    """limit 运行时可改：claim 时现读现值、排队请求不因设置变更被
    fail-fast（与本地路径「请求携带值 vs 现值」契约式校验有意不同的核心
    语义）——limit 2→1 后按新值 skip、回调 2 后放行；请求行上的 enqueue
    时 audit 值（2）从不被强制。"""
    workspace_id, node_key = "ws-1149-mut", "package"
    _seed_code_lane(job_db, workspace_id, node_key, limit=2, job_ids=["job-m1", "job-m2"])
    first = _enqueue_code(job_db, workspace_id, "job-m1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-m2", node_key, order=1)
    _register_code_worker("worker-1149-mut")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    round1 = claim_batch_with_retry(pool, "worker-1149-mut", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round1.claims] == ["job-m1"]  # 占 1/2

    _set_node_limit(job_db, workspace_id, node_key, 1)  # 收紧 2→1
    round2 = claim_batch_with_retry(pool, "worker-1149-mut", None, None, limit=1, code_limit=1)
    assert round2.claims == ()
    assert round2.skip_reasons.get("node_limit_full") == 1  # 计数 1 >= 新值 1
    assert _request_state(job_db, second) == "queued"  # 留队列，不 fail-fast
    assert _request_state(job_db, first) == "claimed"
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select node_concurrency_limit from agent_execution_requests where execution_id=%s",
            (second,),
        ).fetchone()
    assert int(row["node_concurrency_limit"]) == 2  # enqueue 时 audit 值，从不强制

    _set_node_limit(job_db, workspace_id, node_key, 2)  # 放宽回 2
    round3 = claim_batch_with_retry(pool, "worker-1149-mut", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round3.claims] == ["job-m2"]  # 计数 1 < 2


def test_agent_lease_at_same_node_does_not_block_remote_code_claim(job_db) -> None:
    """用例 9（#1167 P2）：同 node_key 的 agent:<id> 租约（旧 revision 的
    agent job 在跑）不计入 code 节点额度——limit=1 且 agent 租约占位时
    远程 code claim 仍放行；修前计数不筛执行类型，一个旧 agent 执行就
    阻塞所有新 remote code claim。"""
    workspace_id, node_key = "ws-1167", "review"
    _register_code_worker("worker-1167")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 旧 revision 的 agent job：同 node_key 的 agent 请求被认领，落下真实
    # agent:<id> 租约（非 code 形态）。
    seed_request(job_db, job_id="job-1167-agent", workspace_id=workspace_id, node_key=node_key)
    round1 = claim_batch_with_retry(pool, "worker-1167", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round1.claims] == ["job-1167-agent"]
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select executor_id from executor_leases where job_id=%s", ("job-1167-agent",)
        ).fetchone()
    assert row is not None
    agent_executor_id = str(row["executor_id"])
    assert agent_executor_id.startswith("agent:")
    assert not agent_executor_id.startswith("agent:code:")
    assert agent_executor_id != CODE_EXECUTOR_ID

    # 新 revision 的 code job：同 node_key 配 limit=1——agent 租约不占
    # code 额度，claim 放行（修前在此 skip node_limit_full）。
    _seed_code_lane(job_db, workspace_id, node_key, limit=1, job_ids=["job-1167-code"])
    code_execution = _enqueue_code(job_db, workspace_id, "job-1167-code", node_key)
    round2 = claim_batch_with_retry(pool, "worker-1167", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round2.claims] == ["job-1167-code"]
    assert round2.skip_reasons.get("node_limit_full", 0) == 0
    assert _request_state(job_db, code_execution) == "claimed"
    # 合并计数语义不变：两张租约都在（无过滤计数=2），code 额度只看
    # 自己的 code 形态租约（1/1 占满）。
    assert _active_node_lease_count(job_db, workspace_id, node_key) == 2
    with job_db._connect_read() as conn:
        code_row = conn.execute(
            "select executor_id from executor_leases where job_id=%s", ("job-1167-code",)
        ).fetchone()
    # code 形态的精确字面量（与计数 SQL 的 like 前缀同形，钉住两侧契约）。
    assert code_row is not None
    assert str(code_row["executor_id"]) == "agent:code:package"


def _lease_executor_id(job_db, job_id: str) -> str:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select executor_id from executor_leases where job_id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return str(row["executor_id"])


def test_node_limit_counting_matrix_by_lease_form(job_db) -> None:
    """用例 10（#1167 矩阵）：计数口径表的形态钉子——三种占位形态 × limit=1，
    每形态独立 (workspace, node_key) 搭建互不污染，占位租约的 executor_id
    形态本身入断言（口径表「行形态」维度）：

    - 本地 code lease（``executor_id='code'``）占位 → 远程 code claim 拒；
    - 远程 code lease（``agent:code:%``）占位 → 第二个远程 code claim 拒；
    - agent lease（``agent:<id>``，非 code 形态）占位 → 远程 code claim 放行。
    """
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 形态 1：本地 code lease 占位 → 拒（口径表行 1 计入）。
    ws1, node1 = "ws-1167-m1", "pkg"
    _seed_code_lane(job_db, ws1, node1, limit=1, job_ids=["job-m1-occ", "job-m1-probe"])
    _enqueue_code(job_db, ws1, "job-m1-probe", node1)
    _register_code_worker("worker-1167-m1")
    assert repo.try_claim(_local_claim_request(ws1, "job-m1-occ", node1, 1)) is not None
    assert _lease_executor_id(job_db, "job-m1-occ") == CODE_EXECUTOR_ID
    round1 = claim_batch_with_retry(pool, "worker-1167-m1", None, None, limit=1, code_limit=1)
    assert round1.claims == ()
    assert round1.skip_reasons.get("node_limit_full") == 1
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id=%s", ("job-m1-probe",)
        ).fetchone()
    assert row is not None and row["state"] == "queued"  # 留队列
    # 收尾：清掉本形态的遗留 queued 请求——形态间 (workspace, node) 互不
    # 影响，但批扫描窗口共享，skip 留队列的请求会占据后续形态的候选名额。
    with job_db.connect() as conn:
        conn.execute("delete from agent_execution_requests where job_id=%s", ("job-m1-probe",))

    # 形态 2：远程 code lease 占位 → 第二个拒（口径表行 2 计入）。
    ws2, node2 = "ws-1167-m2", "pkg"
    _seed_code_lane(job_db, ws2, node2, limit=1, job_ids=["job-m2-a", "job-m2-b"])
    first = _enqueue_code(job_db, ws2, "job-m2-a", node2, order=0)
    second = _enqueue_code(job_db, ws2, "job-m2-b", node2, order=1)
    _register_code_worker("worker-1167-m2")
    round2 = claim_batch_with_retry(pool, "worker-1167-m2", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round2.claims] == ["job-m2-a"]
    assert _lease_executor_id(job_db, "job-m2-a").startswith("agent:code:")
    round3 = claim_batch_with_retry(pool, "worker-1167-m2", None, None, limit=1, code_limit=1)
    assert round3.claims == ()
    assert round3.skip_reasons.get("node_limit_full") == 1
    assert _request_state(job_db, first) == "claimed"
    assert _request_state(job_db, second) == "queued"
    with job_db.connect() as conn:
        conn.execute("delete from agent_execution_requests where job_id=%s", ("job-m2-b",))

    # 形态 3：agent lease 占位 → 放行（口径表行 3 不计入）。
    ws3, node3 = "ws-1167-m3", "review"
    seed_request(job_db, job_id="job-m3-agent", workspace_id=ws3, node_key=node3)
    _register_code_worker("worker-1167-m3")
    round4 = claim_batch_with_retry(pool, "worker-1167-m3", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round4.claims] == ["job-m3-agent"]
    agent_form = _lease_executor_id(job_db, "job-m3-agent")
    assert agent_form.startswith("agent:") and not agent_form.startswith("agent:code:")
    _seed_code_lane(job_db, ws3, node3, limit=1, job_ids=["job-m3-code"])
    _enqueue_code(job_db, ws3, "job-m3-code", node3)
    round5 = claim_batch_with_retry(pool, "worker-1167-m3", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in round5.claims] == ["job-m3-code"]
    assert round5.skip_reasons.get("node_limit_full", 0) == 0
    assert _lease_executor_id(job_db, "job-m3-code").startswith("agent:code:")


def test_node_limit_matrix_boundary_legacy_agent_id_with_colon_counts(job_db) -> None:
    """用例 11（#1167 口径表边界行，评审 P3-1）：named ``code:x`` 的存量
    Agent 走 kind=agent claim 会写出 ``agent:code:x`` 租约——``like
    'agent:code:%'`` 前缀无法区分，该租约**被计入** code 额度（本用例
    直接 INSERT 模拟存量形态租约，service 写边界只封新值、不迁移存量，
    #1173）。

    断言方向 = 计入（诚实边界而非缺陷修复）：错误方向是「一个存量
    agent 租约消耗一个 code 名额」——保守方向（占位 → 拒 → 留队列重
    试），永不超收；前提不成立时行为如实钉住。"""
    ws, node = "ws-1167-b4", "review"
    _seed_code_lane(job_db, ws, node, limit=1, job_ids=["job-b4-occ", "job-b4-probe"])
    probe_execution = _enqueue_code(job_db, ws, "job-b4-probe", node)
    _register_code_worker("worker-1167-b4")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 模拟存量：named ``code:x`` 的 agent job 已有 kind=agent 租约在跑
    # （claim_promote 的 kind=agent 分支写 ``agent:<agent_id>``）。
    _seed_code_job(job_db, ws, "job-b4-legacy", node)
    with job_db.connect() as conn:
        legacy_run = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at, execution_generation)"
            " values (%s, %s, 'running', '[]', 'logs/legacy.log', '', '',"
            " current_timestamp, 0) returning id",
            ("job-b4-legacy", node),
        ).fetchone()
        conn.execute(
            """
            insert into executor_leases(
              id, execution_id, executor_id, workspace_id, job_id,
              node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at,
              execution_generation)
            values (%s, %s, 'agent:code:x', %s, %s, %s, %s, 'active',
                    current_timestamp, current_timestamp, current_timestamp + interval '60s', 0)
            """,
            (
                "lease-1167-b4",
                "exec-1167-b4",
                ws,
                "job-b4-legacy",
                node,
                int(legacy_run["id"]),
            ),
        )
    assert _lease_executor_id(job_db, "job-b4-legacy") == "agent:code:x"

    # 前缀命中 → 计入 code 额度 → limit=1 下远程 code claim 被拒（保守）。
    round = claim_batch_with_retry(pool, "worker-1167-b4", None, None, limit=1, code_limit=1)
    assert round.claims == ()
    assert round.skip_reasons.get("node_limit_full") == 1
    assert _request_state(job_db, probe_execution) == "queued"
