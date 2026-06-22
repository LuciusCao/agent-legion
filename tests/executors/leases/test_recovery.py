from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import _sync_job_status
from server.app.executors.leases import ExecutorLeaseRepository, _sqlite_timestamp
from server.app.executors.models import (
    LeaseClaimRequest,
)
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import (
    _bind_executor_to_node,
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
            "update job_nodes set status='completed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        _sync_job_status(conn, job_id)
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
            "update job_nodes set status='completed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "node_b"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        _sync_job_status(conn, job_id)
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
            "update job_nodes set status='completed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set execution_paused=1, status='paused' where id=?",
            (job_id,),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        _sync_job_status(conn, job_id)
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
            "update job_nodes set status='failed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='completed' where job_id=? and node_key=?",
            (job_id, "node_b"),
        )
        conn.execute("commit")

    with queries.connect() as conn:
        _sync_job_status(conn, job_id)
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
    _bind_executor_to_node(
        queries,
        workspace_id,
        "exec-requeue",
        1,
        node_key="node_b",
        local_limit=1,
        workflow_key="question_comprehension_info",
    )
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='queued' where id=?",
            (job_id,),
        )
        conn.execute("commit")

    request = LeaseClaimRequest(
        executor_id="exec-requeue",
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="question_comprehension_info",
        node_key="node_b",
        capability="review_keywords",
        log_path="logs/node_b.log",
        lease_ttl_seconds=60,
        global_capacity=10,
        local_node_limit=1,
    )

    with queries.connect() as conn:
        claimed = claim_lease(conn, request, queries.path.parent)
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
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=?",
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
            "update job_nodes set status='failed' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "node_b"),
        )
        conn.execute(
            "update jobs set status='running' where id=?",
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
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=?",
            (job_id,),
        )
        cursor = conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (?, ?, 'running', ?, ?)
            """,
            (job_id, "node_a", _sqlite_timestamp(datetime.now(UTC)), "/tmp/run.log"),
        )
        node_run_id = cursor.lastrowid
        conn.execute(
            """
            insert into executor_leases(
                id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                "lease-1",
                "exec-1",
                "exec-active",
                workspace_id,
                job_id,
                "question_comprehension_info",
                "node_a",
                node_run_id,
                _sqlite_timestamp(datetime.now(UTC)),
                _sqlite_timestamp(datetime.now(UTC)),
                _sqlite_timestamp(datetime.now(UTC) + timedelta(seconds=60)),
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
    now_str = _sqlite_timestamp(now)
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "node_a"),
        )
        conn.execute(
            "update jobs set status='running' where id=?",
            (job_id,),
        )
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (?, ?, 'running', ?, ?)
            """,
            (job_id, "node_a", now_str, "/tmp/orphan.log"),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(now)

    assert recovered == [job_id]
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "node_a"),
        ).fetchone()
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_message"] == "orphaned recovery"
    assert run["finished_at"] is not None
