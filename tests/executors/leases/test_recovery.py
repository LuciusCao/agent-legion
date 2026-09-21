from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.db.transaction import write_transaction
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import sync_job_status
from server.app.executors._lease_write_paths import _recover_orphaned_job
from server.app.executors.leases import ExecutorLeaseRepository, database_timestamp
from server.app.executors.models import (
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
from server.app.workflows.sharding import try_start_shard
from tests.executors.leases.helpers import (
    _claim_request,
    _set_node_limit,
    _setup_workspace,
)


def test_sync_job_status_returns_queued_when_nodes_remain(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-queued", "exec-queued", 1, node_keys=["node_a", "node_b"]
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        sync_job_status(conn, job_id)
        conn.commit()

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"


def test_sync_job_status_keeps_running_when_another_node_is_running(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-concurrent", "exec-concurrent", 2, node_keys=["node_a", "node_b"]
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_b"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        sync_job_status(conn, job_id)
        conn.commit()

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"


def test_sync_job_status_keeps_paused_when_execution_paused(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-paused", "exec-paused", 1, node_keys=["node_a", "node_b"]
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set execution_paused=1, status='paused' where id=%s",
            (job_id,),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        sync_job_status(conn, job_id)
        conn.commit()

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "paused"


def test_sync_job_status_failed_when_any_node_failed(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-failed", "exec-failed", 1, node_keys=["node_a", "node_b"]
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='failed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key=%s",
            (job_id, "node_b"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        sync_job_status(conn, job_id)
        conn.commit()

    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"


def test_claim_lease_transitions_queued_job_back_to_running(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-requeue", "exec-requeue", 1, node_keys=["node_a", "node_b"]
    )
    _set_node_limit(queries, workspace_id, "demo_workflow", "node_b", 1)
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='queued' where id=%s",
            (job_id,),
        )
        conn.execute("commit")

    request = LeaseClaimRequest(
        executor_id="exec-requeue",
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
        node_key="node_b",
        capability="review_keywords",
        log_path="logs/node_b.log",
        lease_ttl_seconds=60,
        global_capacity=10,
        local_node_limit=1,
    )

    with queries.connect() as conn:
        claimed = claim_lease(conn, request, queries.jobs_dir.parent)
        conn.commit()

    assert claimed is not None
    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"


def test_recover_orphaned_running_jobs_returns_them_to_queued(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-orphan", "exec-orphan", 1, node_keys=["node_a", "node_b"]
    )
    # simulate a stuck state: job running, node_a running, but no active lease
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=%s",
            (job_id,),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))

    assert recovered == [job_id]
    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    node = queries.get_job_node(job_id, "node_a")
    assert node is not None
    assert node["status"] == "pending"


def test_recover_orphaned_running_jobs_preserves_failed_job_status(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-orphan-failed", "exec-orphan-failed", 1, node_keys=["node_a", "node_b"]
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='failed' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_b"),
        )
        conn.execute(
            "update jobs set status='running' where id=%s",
            (job_id,),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))

    assert recovered == [job_id]
    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    node = queries.get_job_node(job_id, "node_b")
    assert node is not None
    assert node["status"] == "pending"


def test_recover_skips_jobs_with_active_lease(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-active", "exec-active", 1, node_keys=["node_a", "node_b"]
    )
    # insert an active lease for the job
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=%s",
            (job_id,),
        )
        cursor = conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (%s, %s, 'running', %s, %s)
            returning id
            """,
            (job_id, "node_a", database_timestamp(datetime.now(UTC)), "/tmp/run.log"),
        )
        node_run_id = cursor.fetchone()["id"]
        conn.execute(
            """
            insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) values (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                "lease-1",
                "exec-1",
                "exec-active",
                workspace_id,
                job_id,
                "node_a",
                node_run_id,
                database_timestamp(datetime.now(UTC)),
                database_timestamp(datetime.now(UTC)),
                database_timestamp(datetime.now(UTC) + timedelta(seconds=60)),
            ),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))

    assert recovered == []
    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"


def test_recover_orphaned_running_jobs_marks_running_node_runs_failed(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-orphan-runs", "exec-orphan-runs", 1, node_keys=["node_a", "node_b"]
    )
    now = datetime.now(UTC)
    now_str = database_timestamp(now)
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=%s",
            (job_id,),
        )
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (%s, %s, 'running', %s, %s)
            """,
            (job_id, "node_a", now_str, "/tmp/orphan.log"),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(now)

    assert recovered == [job_id]
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        ).fetchone()
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_message"] == "orphaned recovery"
    assert run["finished_at"] is not None


def test_recover_skips_job_when_lease_claimed_concurrently(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    """Replay the race: candidate SELECT sees an orphaned job, then a claim
    for another node of the same job commits before the recovery UPDATE.

    The guarded per-job recovery must leave the freshly claimed node (and the
    still-orphaned one) untouched; the next sweep recovers the orphan once the
    lease is gone.
    """
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-recover-race",
        "exec-recover-race",
        2,
        node_keys=["node_a", "node_b"],
        local_limit=None,
    )
    # Orphaned state: job running, node_a running with no lease; node_b pending.
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "node_a"),
        )
        conn.execute("update jobs set status='running' where id=%s", (job_id,))
        conn.execute("commit")

    now_str = database_timestamp(datetime.now(UTC))
    with write_transaction(queries.dsn_identity) as conn1:
        candidates = conn1.execute(
            """
            select j.id
            from jobs j
            where j.status='running'
              and not exists (
                  select 1 from executor_leases l
                  where l.job_id = j.id and l.status='active'
              )
            """
        ).fetchall()
        assert job_id in [str(row["id"]) for row in candidates]
        # A concurrent claim for node_b commits between SELECT and UPDATE.
        claim = repo_a.try_claim(
            _claim_request(
                workspace_id,
                job_id,
                node_key="node_b",
                executor_id="exec-recover-race",
                local_node_limit=None,
            )
        )
        assert claim is not None
        assert _recover_orphaned_job(conn1, job_id, now_str) is False

    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None and node_a["status"] == "running"
    node_b = queries.get_job_node(job_id, "node_b")
    assert node_b is not None and node_b["status"] == "running"
    with queries.connect() as conn:
        run = conn.execute(
            "select status from node_runs where id=%s", (claim.node_run_id,)
        ).fetchone()
    assert run is not None and run["status"] == "running"

    # Once the lease is released, the next sweep recovers the orphan normally.
    assert repo_a.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))
    assert recovered == [job_id]
    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None and node_a["status"] == "pending"


def test_claim_stamps_running_node_with_current_generation(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    """EXEC-GENERATION-001 审查 P2-b：claim 翻 running 必须给 job_nodes 行盖
    当前代次戳（与 park_awaiting_approval 对称）。

    兄弟节点 rerun 把代次 bump 到 1 后，旁支 pending 行（旧戳 0）上的 claim
    必须盖新戳 1；该行随后成孤儿（lease 消失）时，recover 的代次闸门认戳
    放行复位——旧实现不盖戳，recover 会拒绝，节点永久卡 running。
    """
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-claim-stamp",
        "exec-claim-stamp",
        1,
        node_key="node_a",
        node_keys=["node_a", "node_b"],
    )
    with queries.lease_guarded_mutation(
        job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        mark_nodes_for_rerun(conn, job_id, ["node_b"], {"node_b": []})
    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None and int(node_a["execution_generation"]) == 0  # 旁支行旧戳

    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key="node_a",
            executor_id="exec-claim-stamp",
            execution_generation=1,
        )
    )
    assert claim is not None
    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None
    assert node_a["status"] == "running"
    assert int(node_a["execution_generation"]) == 1  # 盖了 jobs 现值戳

    # 孤儿化（lease 消失、行仍 running）后 recover 必须认戳复位。
    with queries.connect() as conn:
        conn.execute("update executor_leases set status='released' where job_id=%s", (job_id,))
        conn.execute("commit")
    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))
    assert recovered == [job_id]
    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None and node_a["status"] == "pending"


def test_recover_refuses_stale_generation_running_row(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    """代次闸门的另一半：旧戳 running 孤儿行（戳 0 < jobs 现值 1）属于已被
    重置 supersede 的状态，recover 不得复位它（该行的归宿由重置侧负责）。"""
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-stale-stamp",
        "exec-stale-stamp",
        1,
        node_key="node_a",
        node_keys=["node_a", "node_b"],
    )
    with queries.lease_guarded_mutation(
        job_id, datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        mark_nodes_for_rerun(conn, job_id, ["node_b"], {"node_b": []})
    with queries.connect() as conn:
        # 旧戳 running 孤儿：节点在 bump 前翻的 running（不带新戳）、无 lease。
        conn.execute(
            "update job_nodes set status='running' where job_id=%s and node_key='node_a'",
            (job_id,),
        )
        conn.execute("update jobs set status='running' where id=%s", (job_id,))
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))

    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None
    assert node_a["status"] == "running"  # 旧戳行被拒绝复位
    assert int(node_a["execution_generation"]) == 0
    # job 本身仍被 sweep 认领（无 lease 的状态重推导照常跑），行不动。
    assert recovered == [job_id]
    job = queries.get_job(job_id)
    assert job is not None and job["status"] == "running"


def test_try_start_shard_stamps_generation(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    """shard claim 路径的 job_nodes 翻转同样盖戳（try_start_shard 的
    execution_generation 参数）——shard 节点成孤儿时走同一个 recover 闸门。"""
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-shard-stamp",
        "exec-shard-stamp",
        1,
        node_key="node_a",
        node_keys=["node_a"],
    )
    del workspace_id
    with queries.connect() as conn:
        conn.execute(
            "insert into node_shards(job_id, node_key, shard_index, input_json)"
            " values (%s, 'node_a', 0, '{}')",
            (job_id,),
        )
        conn.execute("update jobs set execution_generation=1 where id=%s", (job_id,))
        conn.execute("commit")
        started = try_start_shard(
            conn,
            job_id,
            "node_a",
            0,
            "exec-shard-1",
            database_timestamp(datetime.now(UTC)),
            execution_generation=1,
        )
        assert started is True
        conn.execute("commit")
    node_a = queries.get_job_node(job_id, "node_a")
    assert node_a is not None
    assert node_a["status"] == "running"
    assert int(node_a["execution_generation"]) == 1
