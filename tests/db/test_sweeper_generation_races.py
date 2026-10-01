"""EXEC-GENERATION-001：两个 queued 请求 sweeper 的代次协议交错测试（#645 评审 P1）。

``fail_stale_definition_requests`` 与 ``fail_unclaimable_model_requests`` 原本完全
在协议外：扫描即持 queued 请求行锁（FOR UPDATE）再写 job_nodes/jobs，与 mutation
侧（job-mutation advisory → jobs 行 → job_nodes 行 → ``_cancel_queued_sql`` 取消
queued 请求）构成 AB-BA；且对旧代次请求按 fail 处理，mutation 提交后
``record_failed_node_without_execution`` 的 ``status in ('pending','ready','stale')``
守卫恰好匹配新重置的 pending 行 → 新代次节点被翻 failed。改造后两者与
``sweep_expired_claims`` 同款协议成员：全库批序 (hashtext('agent-ws:' || ws),
job_id) 逐 job 取 job-mutation 锁 → 请求行代次 CAS——旧代次走取消语义（不动
job_nodes），代次相符维持 fail。

手法比照 test_execution_generation_races.py：TIMED_DATABASE_URL 带
deadlock_timeout=50ms + lock_timeout=5s，同步点走 pg_locks 观测（非裸 sleep），
thread.join(timeout=30) 后断言线程已死防假绿。
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
from server.app.agent_broker.manifest_trim import cancel_request
from server.app.agent_broker.unclaimable import fail_unclaimable_model_requests
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection
from server.app.jobs.atomic_mutations import lease_guarded_mutation, mark_nodes_for_rerun
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import insert_job_rows
from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

_separator = "&" if "?" in BASE_DATABASE_URL else "?"
TIMED_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{_separator}options="
    f"{quote(f'-csearch_path={TEST_SCHEMA} -cdeadlock_timeout=50ms -clock_timeout=5s', safe='')}"
)


def _definition(**overrides: Any) -> AgentDefinition:
    values: dict[str, Any] = {
        "capability": "generate",
        "runtime": "pi",
        "skill": "question/generate",
        "requires_labels": {"arch": "arm64"},
    }
    values.update(overrides)
    return AgentDefinition(**values)


def _seed_agent_lane(job_db, *, workspace_id: str, job_id: str, node_key: str = "generate") -> None:
    """catalog + workspace/job/node/route/capacity，但尚不入队请求。"""
    replace_agent_catalog(workspace_id, {"generator-v1": _definition()})
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
            agent_definition_hash=_definition().definition_hash(),
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


def _register_worker(worker_id: str, *, models: list[dict[str, str]]) -> None:
    AgentWorkerRegistry(TIMED_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        capabilities=["generate"],
        models=models,
        max_concurrency=10,
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """B 侧线程：跑完整 sweeper，结果/异常都收进 outcome（冲突也是数据）。"""
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


def _fetchone(sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with read_connection(TIMED_DATABASE_URL) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return dict(row)


def _count(sql: str, params: tuple[Any, ...]) -> int:
    with read_connection(TIMED_DATABASE_URL) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row["cnt"])


def _rerun_sibling_bumps_generation(job_id: str) -> None:
    """真实 mutation：rerun 兄弟节点 bump 代次，不触碰 generate 的 queued 请求
    （generate 不在重置闭包内，``_cancel_queued_sql`` 不覆盖它）。"""
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        mark_nodes_for_rerun(conn, job_id, ["sibling"], {"sibling": []})


def _assert_stale_request_cancelled(job_id: str, execution_id: str) -> None:
    """旧代次请求的收尾面：请求被取消（trim 落戳），job_nodes/jobs 绝不被翻。"""
    request = _fetchone(
        "select state, manifest_json from agent_execution_requests where execution_id=%s",
        (execution_id,),
    )
    assert request["state"] == "cancelled"
    assert '"trimmed": true' in str(request["manifest_json"])
    node = _fetchone(
        "select status, execution_generation from job_nodes where job_id=%s and node_key='generate'",
        (job_id,),
    )
    assert node["status"] == "pending"  # sweeper 不动 job_nodes
    jobs = _fetchone("select status, execution_generation from jobs where id=%s", (job_id,))
    assert jobs["status"] != "failed"
    assert int(jobs["execution_generation"]) == 1  # mutation 的 bump 原样保留
    # 取消语义不写合成失败 run（record_failed_node_without_execution 被跳过）。
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 0


# ---------------------------------------------------------------------------
# 1. stale-definition sweeper：旧代次请求走取消语义
# ---------------------------------------------------------------------------


def test_stale_definition_sweeper_cancels_stale_generation_request(job_db) -> None:
    """钉住「stale-definition sweeper 的代次 CAS」：请求落戳代次 0，mutation
    （兄弟节点 rerun）bump 到 1 后，sweeper 必须按 mutation 侧同语义取消请求
    （cancelled + manifest trim），不翻 job_nodes、不写合成失败 run、不把 job
    翻 failed。最终状态 == 串行序「rerun → sweeper 取消旧代次请求」。

    突变自检：摘掉 lock_sweep_candidate 的 CAS 分支后请求被判 failed、节点被
    翻 failed、jobs 翻 failed——本案例断言全面变红。"""
    job_id = "sw1-job"
    _seed_agent_lane(job_db, workspace_id="sw1-ws", job_id=job_id)
    execution_id = _enqueue(job_db, workspace_id="sw1-ws", job_id=job_id, generation=0)
    _add_node(job_db, job_id, "sibling")
    # 定义失效（sweeper 的选中条件成立），随后 mutation bump 代次。
    replace_agent_catalog("sw1-ws", {"generator-v1": _definition(skill="question/generate-v2")})
    _rerun_sibling_bumps_generation(job_id)
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.fail_stale_definition_requests() == []

    _assert_stale_request_cancelled(job_id, execution_id)


# ---------------------------------------------------------------------------
# 2. unclaimable sweeper：旧代次请求走取消语义
# ---------------------------------------------------------------------------


def test_unclaimable_sweeper_cancels_stale_generation_request(job_db) -> None:
    """钉住「unclaimable sweeper 的代次 CAS」：与案例 1 同构，但选中条件是
    无可认领 Worker（model 不匹配）；旧代次请求同样被取消而非判失败。

    突变自检：摘掉 CAS 后请求 done/failed、节点翻 failed——断言变红。"""
    job_id = "sw2-job"
    _seed_agent_lane(job_db, workspace_id="sw2-ws", job_id=job_id)
    execution_id = _enqueue(job_db, workspace_id="sw2-ws", job_id=job_id, generation=0)
    _add_node(job_db, job_id, "sibling")
    _register_worker("worker-sw2", models=[{"provider": "gateway", "model": "other-model"}])
    _rerun_sibling_bumps_generation(job_id)
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert fail_unclaimable_model_requests(broker) == []

    _assert_stale_request_cancelled(job_id, execution_id)


# ---------------------------------------------------------------------------
# 3/4. sweeper × mutation AB-BA 回归
# ---------------------------------------------------------------------------


def _drive_mutation_past_node_write(job_id: str) -> tuple[contextlib.ExitStack, Any, int]:
    """A 侧（主线程）：lease_guarded_mutation 持锁 + bump 代次 + 把 generate 行
    翻成 stale（持 job_nodes 行锁），事务不提交；返回 (stack, conn, 新代次)。
    mutation 侧取消 queued 请求的步骤留给调用方在 B 进入锁等待后执行——这
    正是构造 AB-BA 所需的确定性交错。"""
    stack = contextlib.ExitStack()
    conn = stack.enter_context(
        lease_guarded_mutation(
            TIMED_DATABASE_URL, job_id, datetime.now(UTC), reject_running_nodes=True
        )
    )
    bumped = conn.execute(
        "update jobs set status='queued', execution_generation=execution_generation+1,"
        " updated_at=current_timestamp where id=%s returning execution_generation",
        (job_id,),
    ).fetchone()
    assert bumped is not None
    generation = int(bumped["execution_generation"])
    conn.execute(
        "update job_nodes set status='stale', stale_reason='upstream rerun',"
        " execution_generation=%s where job_id=%s and node_key='generate'",
        (generation, job_id),
    )
    return stack, conn, generation


def _assert_mutation_write_preserved(job_id: str, generation: int) -> None:
    """双方合理收尾的收尾面：mutation 的写原样保留，sweeper 零副作用。"""
    node = _fetchone(
        "select status, execution_generation from job_nodes where job_id=%s and node_key='generate'",
        (job_id,),
    )
    assert node["status"] == "stale"
    assert int(node["execution_generation"]) == generation
    jobs = _fetchone("select status, execution_generation from jobs where id=%s", (job_id,))
    assert jobs["status"] == "queued"
    assert int(jobs["execution_generation"]) == generation


def test_stale_definition_sweeper_vs_mutation_no_ab_ba(job_db) -> None:
    """钉住「stale-definition sweeper 不再与 mutation 构成 AB-BA」。

    A = 持 job-mutation 锁的 mutation（已 bump、持 job_nodes('generate') 行锁，
    尚未取消 queued 请求），B = fail_stale_definition_requests。新代码下 B 的
    扫描无锁、第一步就等 A 的 advisory 锁；A 提交后 B 看到请求已被 mutation
    取消（state 不再是 queued）→ 零动作收尾，无 40P01。旧代码下 B 扫描即持
    请求行锁，随后写 job_nodes 卡在 A 的行锁上，A 的取消再卡回 B 的请求行锁
    ——成环（deadlock_timeout=50ms 放大为立现；至少同步点必超时）。"""
    job_id = "sw3-job"
    _seed_agent_lane(job_db, workspace_id="sw3-ws", job_id=job_id)
    execution_id = _enqueue(job_db, workspace_id="sw3-ws", job_id=job_id, generation=0)
    replace_agent_catalog("sw3-ws", {"generator-v1": _definition(skill="question/generate-v2")})
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    stack, conn_a, generation = _drive_mutation_past_node_write(job_id)
    thread, outcome = _start(broker.fail_stale_definition_requests)
    try:
        _await_job_mutation_waiter(job_id)  # B 卡在 job-mutation advisory 锁上
        # mutation 侧 _cancel_queued_sql 的等价动作：同请求行、同取消语义。
        cancel_request(conn_a, execution_id)
        stack.close()  # 提交 mutation：代次 bump 生效
    finally:
        stack.close()
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") == []
    request = _fetchone(
        "select state from agent_execution_requests where execution_id=%s", (execution_id,)
    )
    assert request["state"] == "cancelled"
    _assert_mutation_write_preserved(job_id, generation)


def test_unclaimable_sweeper_vs_mutation_no_ab_ba(job_db) -> None:
    """钉住「unclaimable sweeper 不再与 mutation 构成 AB-BA」：与案例 3 同构，
    选中条件换为无可认领 Worker。新旧代码的收尾/成环行为对照同案例 3。"""
    job_id = "sw4-job"
    _seed_agent_lane(job_db, workspace_id="sw4-ws", job_id=job_id)
    execution_id = _enqueue(job_db, workspace_id="sw4-ws", job_id=job_id, generation=0)
    _register_worker("worker-sw4", models=[{"provider": "gateway", "model": "other-model"}])
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    stack, conn_a, generation = _drive_mutation_past_node_write(job_id)
    thread, outcome = _start(lambda: fail_unclaimable_model_requests(broker))
    try:
        _await_job_mutation_waiter(job_id)  # B 卡在 job-mutation advisory 锁上
        cancel_request(conn_a, execution_id)
        stack.close()
    finally:
        stack.close()
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") == []
    request = _fetchone(
        "select state from agent_execution_requests where execution_id=%s", (execution_id,)
    )
    assert request["state"] == "cancelled"
    _assert_mutation_write_preserved(job_id, generation)


# ---------------------------------------------------------------------------
# 5. 代次相符：正常 fail 语义不变
# ---------------------------------------------------------------------------


def test_stale_definition_sweeper_fails_current_generation_request(job_db) -> None:
    """钉住「代次相符时 fail 语义不变」（非零代次版本；0 代次由
    tests/services/test_agent_broker_concurrency.py 的既有用例覆盖）：请求落戳
    代次 == jobs 现值时，sweeper 照旧判失败——请求 done、节点 failed、job
    failed、合成失败 run 落库。"""
    job_id = "sw5-job"
    _seed_agent_lane(job_db, workspace_id="sw5-ws", job_id=job_id)
    _add_node(job_db, job_id, "sibling")
    _rerun_sibling_bumps_generation(job_id)  # 代次 bump 到 1
    # bump 后按新代次入队（定义仍有效，enqueue 的 hash 校验通过），随后定义失效。
    execution_id = _enqueue(job_db, workspace_id="sw5-ws", job_id=job_id, generation=1)
    replace_agent_catalog("sw5-ws", {"generator-v1": _definition(skill="question/generate-v2")})
    broker = AgentExecutionBroker(TIMED_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.fail_stale_definition_requests() == [execution_id]

    request = _fetchone(
        "select state from agent_execution_requests where execution_id=%s", (execution_id,)
    )
    assert request["state"] == "done"
    node = _fetchone(
        "select status, error_message from job_nodes where job_id=%s and node_key='generate'",
        (job_id,),
    )
    assert node["status"] == "failed"
    assert "disabled or changed" in node["error_message"]
    jobs = _fetchone("select status from jobs where id=%s", (job_id,))
    assert jobs["status"] == "failed"
    assert _count("select count(*) as cnt from node_runs where job_id=%s", (job_id,)) == 1
