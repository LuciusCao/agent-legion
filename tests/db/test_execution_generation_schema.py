"""execution_generation 代次列的 schema 行为测试（issue #759，schema v85）。

v85 给五张表加 ``execution_generation`` 镜像列（jobs 为代次事实源，
job_nodes / node_runs / executor_leases / agent_execution_requests 为各
写路径的就地 CAS 戳，EXEC-GENERATION-001）。DDL 走 apply fn
（postgres_schema.sql raw 行顶格，v76/v84 先例），这里钉列存在性、
默认值与幂等性；catalog 平权由 tests/db/test_schema_upgrade_parity.py
覆盖；代次 bump/CAS 的行为测试随协议实现追加到本文件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from server.app.db.migrations.execution_generation import migrate_execution_generation
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_claims import claim_lease
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    CODE_EXECUTOR_ID,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.jobs.workflow_upgrade_mutation import upgrade_job_workflow
from tests.postgres_support import TEST_DATABASE_URL

_EPOCH_TABLES = (
    "jobs",
    "job_nodes",
    "node_runs",
    "executor_leases",
    "agent_execution_requests",
)


def test_execution_generation_columns_exist_with_default_zero() -> None:
    """五张表各有 integer not null default 0 的 execution_generation 列。"""
    with read_connection(TEST_DATABASE_URL) as conn:
        for table in _EPOCH_TABLES:
            row = conn.execute(
                "select data_type, column_default, is_nullable"
                " from information_schema.columns"
                " where table_schema=current_schema() and table_name=%s"
                " and column_name='execution_generation'",
                (table,),
            ).fetchone()
            assert row is not None, table
            assert row["data_type"] == "integer", table
            assert row["is_nullable"] == "NO", table
            assert row["column_default"] == "0", table


@pytest.mark.fresh_schema
def test_migrate_execution_generation_is_idempotent() -> None:
    """apply fn 重复执行无副作用（升级库与 fresh 库同径）。DDL 用例按
    仓库隔离约定走 fresh_schema 完整重建，不把结构漂移泄漏给同 worker。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_execution_generation(conn)
        migrate_execution_generation(conn)


# ---------------------------------------------------------------------------
# 阶段 1b：mutation 侧 bump / 盖戳 / 锁域（EXEC-GENERATION-001）。
# ---------------------------------------------------------------------------


def _seed_job(tmp_path: Path, *, statuses: tuple[str, ...] = ("completed",) * 3):
    """三节点 a/b/c 的 job，节点状态按 ``statuses`` 播种，job 置 completed。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wsgen", default_workflow_key="wfgen")
    job = queries.create_job(
        workflow_key="wfgen",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["a", "b", "c"],
        workspace_id=workspace["id"],
    )
    for key, status in zip(("a", "b", "c"), statuses, strict=True):
        queries.update_job_node(job["id"], key, status=status)
    queries.update_job_status(job["id"], "completed")
    return queries, job


def _generation(queries: JobQueries, job_id: str) -> int:
    return int(queries.get_job(job_id)["execution_generation"])


def _node_rows(queries: JobQueries, job_id: str) -> dict[str, dict]:
    return {n["node_key"]: n for n in queries.list_job_nodes(job_id)}


def test_rerun_bumps_once_and_stamps_reset_nodes(tmp_path: Path) -> None:
    """mark_nodes_for_rerun：jobs 代次恰好 +1，pending/stale 重置行盖新戳。"""
    queries, job = _seed_job(tmp_path)
    with queries.lease_guarded_mutation(
        job["id"], datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        queries.mark_nodes_for_rerun_in_transaction(conn, job["id"], ["b"], {"b": ["c"]})

    assert _generation(queries, job["id"]) == 1
    nodes = _node_rows(queries, job["id"])
    assert nodes["b"]["status"] == "pending"
    assert nodes["b"]["execution_generation"] == 1
    assert nodes["c"]["status"] == "stale"
    assert nodes["c"]["execution_generation"] == 1
    # 未被重置的节点行（a）不动，连同旧代次戳原样保留。
    assert nodes["a"]["status"] == "completed"
    assert nodes["a"]["execution_generation"] == 0


def test_run_to_with_start_bumps_once_across_both_writes(tmp_path: Path) -> None:
    """run-to-with-start 服务组合：mark_nodes_for_rerun bump，set_run_to_control 不 bump。"""
    queries, job = _seed_job(tmp_path, statuses=("completed", "failed", "pending"))
    with queries.lease_guarded_mutation(
        job["id"], datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        queries.mark_nodes_for_rerun_in_transaction(conn, job["id"], ["b"], {"b": []})
        queries.set_run_to_control_in_transaction(conn, job["id"], "c")

    updated = queries.get_job(job["id"])
    assert updated["execution_generation"] == 1
    assert updated["execution_mode"] == "until_node"
    assert updated["target_node_key"] == "c"


def test_apply_run_to_bumps_once_and_stamps_reset_nodes(tmp_path: Path) -> None:
    """run-to（无起始节点）：set_run_to_control 的 bump 分支是唯一 bump 点。"""
    queries, job = _seed_job(tmp_path, statuses=("failed", "failed", "pending"))
    queries.apply_run_to_atomic(job["id"], "c", frozenset({"a", "b", "c"}))

    assert _generation(queries, job["id"]) == 1
    nodes = _node_rows(queries, job["id"])
    for key in ("a", "b", "c"):
        assert nodes[key]["status"] == "pending"
        assert nodes[key]["execution_generation"] == 1


def test_upgrade_mutation_bumps_and_stamps_reinserted_rows(tmp_path: Path) -> None:
    """upgrade mutation（clean）：jobs 代次 +1，重建的 pending 行盖新戳。"""
    queries, job = _seed_job(tmp_path)
    with write_transaction(queries.dsn_identity) as conn:
        upgrade_job_workflow(
            conn,
            job["id"],
            workflow_revision_id="rev-gen",
            workflow_version=2,
            workflow_definition_hash="hash-gen",
            workflow_definition_snapshot_json='{"key": "wfgen"}',
            node_keys=["a", "b", "c"],
            frozen_config_json=None,
        )

    assert _generation(queries, job["id"]) == 1
    nodes = _node_rows(queries, job["id"])
    for key in ("a", "b", "c"):
        assert nodes[key]["status"] == "pending"
        assert nodes[key]["execution_generation"] == 1


def test_resume_job_does_not_bump_generation(tmp_path: Path) -> None:
    """非重置路径：paused→queued 只恢复调度，不动代次。"""
    queries, job = _seed_job(tmp_path, statuses=("completed", "pending", "pending"))
    queries.update_job_status(job["id"], "paused")

    queries.resume_job(job["id"])

    updated = queries.get_job(job["id"])
    assert updated["status"] == "queued"
    assert updated["execution_generation"] == 0


def _held_advisory_keys(conn) -> set[int]:
    """本 backend 持有的单键 advisory 锁（64 位无符号还原，比照
    tests/services/test_agent_broker_claim_locks.py 的观测手法）。"""
    rows = conn.execute(
        "select classid, objid from pg_locks"
        " where locktype='advisory' and objsubid=1 and pid=pg_backend_pid()"
    ).fetchall()
    return {(int(row["classid"]) << 32) | int(row["objid"]) for row in rows}


def test_lease_guarded_mutation_holds_job_mutation_advisory_lock(tmp_path: Path) -> None:
    """lease_guarded_mutation 事务内持有 hashtext('job-mutation:' || job_id) 的 xact 锁。"""
    queries, job = _seed_job(tmp_path)
    with queries.lease_guarded_mutation(
        job["id"], datetime.now(UTC), reject_running_nodes=False
    ) as conn:
        hashed = conn.execute(
            "select hashtext(%s) as k", (f"job-mutation:{job['id']}",)
        ).fetchone()["k"]
        expected = int(hashed) & 0xFFFFFFFFFFFFFFFF
        assert expected in _held_advisory_keys(conn)


# ---------------------------------------------------------------------------
# 阶段 1c：本地 code 池 claim 侧 CAS（EXEC-GENERATION-001）。
# ---------------------------------------------------------------------------


def _seed_claimable_job(tmp_path: Path, node_keys: tuple[str, ...] = ("a", "b")):
    """节点全 pending、job 停在 queued 的可 claim job（不走 _seed_job 的 completed 收尾）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wsgen-claim", default_workflow_key="wfgen")
    job = queries.create_job(
        workflow_key="wfgen",
        source_type="question",
        source_id="Q-claim",
        run_id="batch-claim",
        title="Claim Job",
        node_keys=list(node_keys),
        workspace_id=workspace["id"],
    )
    return queries, job


def _code_claim_request(job: dict, node_key: str, *, generation: int) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=CODE_EXECUTOR_ID,
        global_capacity=4,
        workspace_id=str(job["workspace_id"]),
        job_id=str(job["id"]),
        workflow_key=str(job["workspace_id"]),
        node_key=node_key,
        capability="review_keywords",
        local_node_limit=None,
        lease_ttl_seconds=60,
        log_path=f"logs/{job['id']}-{node_key}.log",
        execution_generation=generation,
    )


def _bump_generation(job_id: str) -> None:
    """模拟 mutation 侧 bump 但不做配套取消——claim 侧 CAS 正是这个缺口的兜底。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            (job_id,),
        )


def test_code_pool_claim_rejects_stale_generation(tmp_path: Path) -> None:
    """期望代次 != jobs 代次 → claim 被拒，不写 node_runs/executor_leases，节点保持 pending。"""
    queries, job = _seed_claimable_job(tmp_path)
    _bump_generation(job["id"])
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)

    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))

    assert claimed is None
    node = queries.get_job_node(job["id"], "a")
    assert node is not None and node["status"] == "pending"
    with read_connection(TEST_DATABASE_URL) as conn:
        runs = conn.execute(
            "select count(*) as cnt from node_runs where job_id=%s", (job["id"],)
        ).fetchone()
        leases = conn.execute(
            "select count(*) as cnt from executor_leases where job_id=%s", (job["id"],)
        ).fetchone()
    assert int(runs["cnt"]) == 0
    assert int(leases["cnt"]) == 0


def test_code_pool_claim_stamps_matching_generation(tmp_path: Path) -> None:
    """代次匹配的成功 claim 给 node_runs / executor_leases 落请求携带的代次。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)

    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))

    assert claimed is not None
    with read_connection(TEST_DATABASE_URL) as conn:
        run = conn.execute(
            "select execution_generation from node_runs where job_id=%s and node_key='a'",
            (job["id"],),
        ).fetchone()
        lease = conn.execute(
            "select execution_generation from executor_leases where job_id=%s and node_key='a'",
            (job["id"],),
        ).fetchone()
    assert run is not None and int(run["execution_generation"]) == 0
    assert lease is not None and int(lease["execution_generation"]) == 0

    # bump 后另一节点以新代次 claim 成功并落新戳。
    _bump_generation(job["id"])
    claimed_b = repo.try_claim(_code_claim_request(job, "b", generation=1))
    assert claimed_b is not None
    with read_connection(TEST_DATABASE_URL) as conn:
        run_b = conn.execute(
            "select execution_generation from node_runs where job_id=%s and node_key='b'",
            (job["id"],),
        ).fetchone()
    assert run_b is not None and int(run_b["execution_generation"]) == 1
    assert _node_rows(queries, job["id"])["b"]["status"] == "running"


def test_code_pool_claim_transaction_holds_job_mutation_lock(tmp_path: Path) -> None:
    """claim_lease 事务内持有 code-pool 与 job-mutation 两把 advisory 锁（池级 → 作业级）。"""
    _queries, job = _seed_claimable_job(tmp_path)
    with write_transaction(TEST_DATABASE_URL) as conn:
        claimed = claim_lease(conn, _code_claim_request(job, "a", generation=0))
        assert claimed is not None
        held = _held_advisory_keys(conn)
        for domain in ("code-pool", f"job-mutation:{job['id']}"):
            hashed = conn.execute("select hashtext(%s) as k", (domain,)).fetchone()["k"]
            assert int(hashed) & 0xFFFFFFFFFFFFFFFF in held, domain


# ---------------------------------------------------------------------------
# 阶段 1d：finish / fail / approval / 清扫路径的代次 CAS（EXEC-GENERATION-001）。
# 核心不变式：旧代次的迟到写绝不能翻转 reset 后的新代次 job_nodes 行；
# 历史行（node_runs）与租约释放照常。
# ---------------------------------------------------------------------------


def _bump_and_reset_node(job_id: str, node_key: str) -> int:
    """模拟 mutation 侧：bump jobs 代次并把节点重置回 pending 盖新戳。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        bumped = conn.execute(
            "update jobs set status='queued', execution_generation=execution_generation+1"
            " where id=%s returning execution_generation",
            (job_id,),
        ).fetchone()
        generation = int(bumped["execution_generation"])
        conn.execute(
            "update job_nodes set status='pending', error_message='',"
            " started_at=null, finished_at=null, execution_generation=%s"
            " where job_id=%s and node_key=%s",
            (generation, job_id, node_key),
        )
    return generation


def _lease_row(job_id: str, node_key: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select status from executor_leases where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None
    return dict(row)


def _run_row(job_id: str, node_key: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select status, error_message from node_runs where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_finish_with_matching_generation_behaves_as_before(tmp_path: Path) -> None:
    """代次匹配的 finish：lease released、node_run 终态、job_nodes 翻转、job 收尾。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))
    assert claimed is not None

    assert repo.finish(claimed.lease_id, ExecutionResult(status="completed", exit_code=0)) is True

    assert _lease_row(job["id"], "a")["status"] == "released"
    assert _run_row(job["id"], "a")["status"] == "completed"
    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "completed"
    assert queries.get_job(job["id"])["status"] == "queued"  # b 仍 pending


def test_finish_with_stale_generation_skips_node_flip(tmp_path: Path) -> None:
    """旧代次迟到 finish：lease/node_run 照常收尾，reset 后的新代次 job_nodes 行不被翻转。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))
    assert claimed is not None
    generation = _bump_and_reset_node(job["id"], "a")

    assert repo.finish(claimed.lease_id, ExecutionResult(status="completed", exit_code=0)) is True

    assert _lease_row(job["id"], "a")["status"] == "released"
    assert _run_row(job["id"], "a")["status"] == "completed"
    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "pending"
    assert node["execution_generation"] == generation
    # sync_job_status 被跳过：job 保持 mutation 留下的 queued，不被翻转。
    assert queries.get_job(job["id"])["status"] == "queued"


def _config_failure_request(job: dict, node_key: str, *, generation: int):
    return ConfigurationFailureRequest(
        workspace_id=str(job["workspace_id"]),
        job_id=str(job["id"]),
        workflow_key=str(job["workspace_id"]),
        node_key=node_key,
        capability="review_keywords",
        log_path=f"logs/{job['id']}-{node_key}.log",
        execution_generation=generation,
    )


def test_fail_without_lease_stale_generation_skips(tmp_path: Path) -> None:
    """代次不符的 fail_without_lease：跳过，节点留 pending 等新代次重新评估。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    _bump_generation(job["id"])

    assert repo.fail_without_lease(_config_failure_request(job, "a", generation=0), "boom") is None

    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "pending"
    with read_connection(TEST_DATABASE_URL) as conn:
        runs = conn.execute(
            "select count(*) as cnt from node_runs where job_id=%s", (job["id"],)
        ).fetchone()
    assert int(runs["cnt"]) == 0


def test_fail_without_lease_matching_generation_fails_node(tmp_path: Path) -> None:
    """代次相符的 fail_without_lease：节点 failed、合成 node_run 落库、job 收尾。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)

    run_id = repo.fail_without_lease(_config_failure_request(job, "a", generation=0), "boom")

    assert run_id is not None
    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "failed"
    assert node["error_message"] == "boom"
    assert _run_row(job["id"], "a")["status"] == "failed"
    assert queries.get_job(job["id"])["status"] == "failed"


def test_park_stamps_generation_and_stale_park_skips(tmp_path: Path) -> None:
    """park 盖当前代次戳（不 bump jobs）；代次过期的候选 park 直接跳过。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    _bump_generation(job["id"])  # jobs 代次 1，节点行仍是旧戳 0

    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=0) is False
    assert _node_rows(queries, job["id"])["a"]["status"] == "pending"

    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=1) is True
    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "awaiting_approval"
    assert node["execution_generation"] == 1
    assert _generation(queries, job["id"]) == 1  # park 不 bump


def _decision(job_id: str, node_key: str, verdict: str) -> dict:
    return {
        "id": f"d-{verdict}",
        "job_id": job_id,
        "node_key": node_key,
        "verdict": verdict,
        "note": "",
        "rework_target": "",
        "decided_by": "user:u1",
    }


def test_approve_after_bare_generation_bump_still_succeeds(tmp_path: Path) -> None:
    """审查 P1：bump 是无条件全局的而重置只盖闭包内节点——闭包外已 park
    的 gate（分支 B 重跑、分支 A 待审）不得被代次误判 brick；旧实现此处
    抛 ApprovalGateConflict。gate 自身被重置时由状态守卫拦截（重置把行
    带离 awaiting_approval；见 races 文件案 5 与 approval flow 测试）。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=0) is True
    _bump_generation(job["id"])

    queries.approve_gate_atomic(_decision(job["id"], "a", "approved"))

    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "completed"
    assert queries.count_approval_decisions(job["id"], "a") == 1


def test_approve_after_gate_reset_conflicts_on_status(tmp_path: Path) -> None:
    """gate 自身被重置（bump + 行回 pending 盖新戳）后，旧决策被状态守卫
    拦住——移除代次比较后这是防「评审员看到的旧 gate」的唯一闸门。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=0) is True
    _bump_and_reset_node(job["id"], "a")

    with pytest.raises(ApprovalGateConflict):
        queries.approve_gate_atomic(_decision(job["id"], "a", "approved"))

    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "pending"
    assert queries.count_approval_decisions(job["id"], "a") == 0


def test_approve_with_fresh_generation_stamp_succeeds(tmp_path: Path) -> None:
    """mutation 重置后按新代次重新 park 的节点可正常 approve。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=0) is True
    generation = _bump_and_reset_node(job["id"], "a")
    assert repo.park_awaiting_approval(job["id"], "a", execution_generation=generation) is True

    queries.approve_gate_atomic(_decision(job["id"], "a", "approved"))

    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "completed"
    assert queries.count_approval_decisions(job["id"], "a") == 1


def test_expire_with_stale_generation_skips_node_flip(tmp_path: Path) -> None:
    """旧代次 lease 的过期清扫：lease expired、node_run failed 照常，job_nodes/jobs 不动。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))
    assert claimed is not None
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update executor_leases set expires_at='2000-01-01'::timestamp where id=%s",
            (claimed.lease_id,),
        )
    generation = _bump_and_reset_node(job["id"], "a")

    assert repo.expire_stale(datetime.now(UTC)) == [claimed.lease_id]

    assert _lease_row(job["id"], "a")["status"] == "expired"
    assert _run_row(job["id"], "a")["status"] == "failed"
    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "pending"
    assert node["execution_generation"] == generation
    assert queries.get_job(job["id"])["status"] == "queued"


def test_recover_skips_stale_generation_running_rows(tmp_path: Path) -> None:
    """orphan 恢复只重置带当前代次戳的 running 行；旧戳行留给新代次的状态机处理。"""
    queries, job = _seed_claimable_job(tmp_path)
    repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    claimed = repo.try_claim(_code_claim_request(job, "a", generation=0))
    assert claimed is not None
    with write_transaction(TEST_DATABASE_URL) as conn:
        # lease 消失（orphan）但节点行还是旧代次戳。
        conn.execute("delete from executor_leases where id=%s", (claimed.lease_id,))
        conn.execute(
            "update jobs set execution_generation=1 where id=%s",
            (job["id"],),
        )

    repo.recover_orphaned_running_jobs(datetime.now(UTC))

    node = _node_rows(queries, job["id"])["a"]
    assert node["status"] == "running"
    assert node["execution_generation"] == 0
