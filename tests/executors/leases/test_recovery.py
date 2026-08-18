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
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
            """,
            (
                "lease-1",
                "exec-1",
                "exec-active",
                workspace_id,
                job_id,
                "demo_workflow",
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
    with write_transaction(queries.path) as conn1:
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
