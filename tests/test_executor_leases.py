import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors._lease_claims import claim_lease
from server.app.executors._lease_control import _sync_job_status
from server.app.executors.leases import ExecutorLeaseRepository, _sqlite_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs.queries import JobQueries
from server.app.storage_paths import ManagedPathError


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    path = tmp_path / "leases.sqlite"
    init_db(path)
    return path


@pytest.fixture
def queries(tmp_db: Path) -> JobQueries:
    jobs_dir = tmp_db.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(tmp_db, jobs_dir)


@pytest.fixture
def repo_a(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)


@pytest.fixture
def repo_b(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)


def _setup_workspace(
    queries: JobQueries,
    name: str,
    executor_id: str,
    workspace_limit: int,
    node_key: str = "review_keywords",
    local_limit: int | None = 1,
    workflow_key: str = "reading_analysis",
    node_keys: list[str] | None = None,
) -> tuple[str, str]:
    workspace = queries.create_workspace(name=name, default_workflow_key=workflow_key)
    workspace_id = workspace["id"]
    job_id = _create_job_in_workspace(
        queries,
        workspace_id,
        node_key=node_key,
        workflow_key=workflow_key,
        node_keys=node_keys,
    )
    _bind_executor_to_node(
        queries,
        workspace_id,
        executor_id,
        workspace_limit,
        node_key=node_key,
        local_limit=local_limit,
        workflow_key=workflow_key,
    )
    return workspace_id, job_id


def _create_job_in_workspace(
    queries: JobQueries,
    workspace_id: str,
    node_key: str = "review_keywords",
    workflow_key: str = "reading_analysis",
    node_keys: list[str] | None = None,
) -> str:
    job = queries.create_job(
        workflow_key=workflow_key,
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        batch_id="",
        title="Test Job",
        node_keys=node_keys or [node_key],
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _bind_executor_to_node(
    queries: JobQueries,
    workspace_id: str,
    executor_id: str,
    workspace_limit: int,
    node_key: str = "review_keywords",
    local_limit: int | None = 1,
    workflow_key: str = "reading_analysis",
) -> None:
    with queries.connect() as conn:
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            on conflict(workspace_id, executor_id) do update set
              concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, executor_id, workspace_limit),
        )
        conn.execute(
            """
            insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)
            values (?, ?, ?, ?)
            on conflict(workspace_id, workflow_key, node_key) do update set
              executor_id=excluded.executor_id
            """,
            (workspace_id, workflow_key, node_key, executor_id),
        )
        if local_limit is not None:
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit)
                values (?, ?, ?, ?)
                on conflict(workspace_id, workflow_key, node_key) do update set
                  concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, workflow_key, node_key, local_limit),
            )


def _claim_request(
    workspace_id: str,
    job_id: str,
    node_key: str = "review_keywords",
    executor_id: str = "local-default",
    global_capacity: int = 2,
    local_node_limit: int | None = 1,
    ttl: int = 60,
    log_path: str = "logs/run.log",
    workflow_key: str = "reading_analysis",
    execution_mode: str = "full",
    target_node_key: str | None = None,
    allowed_node_keys: tuple[str, ...] = (),
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=executor_id,
        global_capacity=global_capacity,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key=workflow_key,
        node_key=node_key,
        capability="review_keywords",
        local_node_limit=local_node_limit,
        lease_ttl_seconds=ttl,
        log_path=log_path,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        target_node_key=target_node_key,
        allowed_node_keys=allowed_node_keys,
    )


def test_two_workers_claim_one_node_only_one_succeeds(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-one", "local-default", workspace_limit=2)
    request = _claim_request(workspace_id, job_id)

    claim_a = repo_a.try_claim(request)
    claim_b = repo_b.try_claim(request)

    winners = [c for c in (claim_a, claim_b) if c is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert isinstance(winner, ClaimedExecution)
    assert winner.workspace_id == workspace_id
    assert winner.job_id == job_id
    assert winner.node_key == "review_keywords"

    # Exactly one node_run and one active lease were persisted.
    with queries.connect() as conn:
        runs = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchall()
        leases = conn.execute(
            "select * from executor_leases where job_id=? and node_key=? and status='active'",
            (job_id, "review_keywords"),
        ).fetchall()
    assert len(runs) == 1
    assert len(leases) == 1


def test_global_capacity_blocks_third_claim(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id_a = _setup_workspace(
        queries, "ws-global", "local-default", workspace_limit=2
    )
    workspace_id_b, job_id_b = _setup_workspace(
        queries, "ws-global-b", "local-default", workspace_limit=2
    )
    executor_id = "local-default"
    global_capacity = 2

    claim_a = repo_a.try_claim(
        _claim_request(
            workspace_id, job_id_a, executor_id=executor_id, global_capacity=global_capacity
        )
    )
    claim_b = repo_b.try_claim(
        _claim_request(
            workspace_id_b, job_id_b, executor_id=executor_id, global_capacity=global_capacity
        )
    )
    claim_c = repo_a.try_claim(
        _claim_request(
            workspace_id, job_id_a, executor_id=executor_id, global_capacity=global_capacity
        )
    )

    assert claim_a is not None
    assert claim_b is not None
    assert claim_c is None


def test_workspace_limit_blocks_second_claim(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id_a = _setup_workspace(
        queries, "ws-limit", "local-default", workspace_limit=1
    )
    job_id_b = _create_job_in_workspace(queries, workspace_id)

    executor_id = "local-default"
    claim_a = repo_a.try_claim(
        _claim_request(workspace_id, job_id_a, executor_id=executor_id, global_capacity=10)
    )
    claim_b = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id_b,
            executor_id=executor_id,
            global_capacity=10,
        )
    )

    assert claim_a is not None
    assert claim_b is None


def test_workspace_a_can_starve_workspace_b_at_global_capacity(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "local-default"
    global_capacity = 2
    workspace_a, job_a1 = _setup_workspace(
        queries, "Workspace A", executor_id, workspace_limit=2, local_limit=None
    )
    job_a2 = _create_job_in_workspace(queries, workspace_a)
    workspace_b, job_b1 = _setup_workspace(
        queries, "Workspace B", executor_id, workspace_limit=2, local_limit=None
    )

    claim_a1 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    claim_a2 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a2,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    claim_b1 = repo_b.try_claim(
        _claim_request(
            workspace_b,
            job_b1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )

    assert claim_a1 is not None
    assert claim_a2 is not None
    assert claim_b1 is None


def test_after_releasing_one_a_lease_b_can_claim(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "local-default"
    global_capacity = 2
    workspace_a, job_a1 = _setup_workspace(
        queries, "Workspace A", executor_id, workspace_limit=2, local_limit=None
    )
    job_a2 = _create_job_in_workspace(queries, workspace_a)
    workspace_b, job_b1 = _setup_workspace(
        queries, "Workspace B", executor_id, workspace_limit=2, local_limit=None
    )

    claim_a1 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    claim_a2 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a2,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    assert claim_a1 is not None
    assert claim_a2 is not None

    finished = repo_a.finish(
        claim_a1.lease_id,
        ExecutionResult(status="completed", exit_code=0),
    )
    assert finished is True

    claim_b1 = repo_b.try_claim(
        _claim_request(
            workspace_b,
            job_b1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    assert claim_b1 is not None


def test_local_node_limit_blocks_same_node_but_allows_other_local_node(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "local-default"
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-local",
        executor_id,
        workspace_limit=10,
        node_key="review_keywords",
        local_limit=1,
    )
    job = queries.get_job(job_id)
    assert job is not None
    other_node_key = "extract_entities"
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (workspace_id, "reading_analysis", other_node_key, executor_id),
        )
        conn.execute(
            "insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (workspace_id, "reading_analysis", other_node_key, 1),
        )
        conn.execute(
            "insert or ignore into job_nodes(job_id, node_key, status) values (?, ?, 'pending')",
            (job_id, other_node_key),
        )

    claim_first = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key="review_keywords",
            executor_id=executor_id,
            global_capacity=10,
        )
    )
    claim_same_node = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key="review_keywords",
            executor_id=executor_id,
            global_capacity=10,
        )
    )
    claim_other_node = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key=other_node_key,
            executor_id=executor_id,
            global_capacity=10,
        )
    )

    assert claim_first is not None
    assert claim_same_node is None
    assert claim_other_node is not None


def test_agent_claim_with_no_local_node_limit_uses_global_and_workspace_only(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "agent-default"
    workspace_id, job_id_a = _setup_workspace(
        queries, "ws-agent", executor_id, workspace_limit=2, local_limit=None
    )
    job_id_b = _create_job_in_workspace(queries, workspace_id)

    claim_a = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id_a,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )
    claim_b = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id_b,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )
    claim_c = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id_a,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )

    assert claim_a is not None
    assert claim_b is not None
    assert claim_c is None


def test_failed_claim_does_not_persist_any_state(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-fail", "local-default", workspace_limit=1)
    repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=1)
    )
    failed = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=1)
    )
    assert failed is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
        nodes = conn.execute(
            "select * from job_nodes where job_id=? and status='running'", (job_id,)
        ).fetchall()
    assert len(runs) == 1
    assert len(leases) == 1
    assert len(nodes) == 1


def test_finish_is_idempotent_and_updates_job_aggregate_status(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-finish", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.path.parent
    run_dir = data_dir / "jobs" / workspace_id / job_id / "runs" / "review_keywords" / "abc"
    session_dir = run_dir / "session"
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("python", "run.py"),
        log_path="logs/updated.log",
        run_dir=str(run_dir),
        session_dir=str(session_dir),
    )
    assert repo_a.finish(claim.lease_id, result) is True
    assert repo_a.finish(claim.lease_id, result) is False

    with queries.connect() as conn:
        lease = conn.execute(
            "select * from executor_leases where id=?", (claim.lease_id,)
        ).fetchone()
        run = conn.execute("select * from node_runs where id=?", (claim.node_run_id,)).fetchone()
        job = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
    assert lease["status"] == "released"
    assert run["status"] == "completed"
    assert run["exit_code"] == 0
    assert run["log_path"] == "logs/updated.log"
    assert run["run_dir"] == run_dir.relative_to(data_dir).as_posix()
    assert run["session_dir"] == session_dir.relative_to(data_dir).as_posix()
    assert job["status"] == "completed"


def test_fail_without_lease_creates_failed_run_and_updates_job_status(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-fail-no-lease", "local-default", workspace_limit=2
    )
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/error.log",
    )
    run_id = repo_a.fail_without_lease(request, "missing binding")
    assert run_id is not None

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=?", (run_id,)).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchone()
        job = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
    assert run["status"] == "failed"
    assert run["error_message"] == "missing binding"
    assert node["status"] == "failed"
    assert job["status"] == "failed"


def test_fail_without_lease_is_idempotent_for_the_same_node(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-fail-no-lease-idempotent", "local-default", workspace_limit=2
    )
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/error.log",
    )

    first_run_id = repo_a.fail_without_lease(request, "missing binding")
    second_run_id = repo_a.fail_without_lease(request, "missing binding")

    assert first_run_id is not None
    assert second_run_id is None
    with queries.connect() as conn:
        run_count = conn.execute(
            "select count(*) from node_runs where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchone()[0]
    assert run_count == 1


def test_heartbeat_extends_lease_expiry(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-heartbeat", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2, ttl=1)
    )
    assert claim is not None

    assert repo_a.heartbeat(claim.lease_id, ttl_seconds=3600) is True
    now = datetime.now(UTC) + timedelta(seconds=2)
    expired = repo_a.expire_stale(now)
    assert claim.lease_id not in expired


def test_expire_stale_releases_expired_leases(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-expire", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2, ttl=1)
    )
    assert claim is not None

    now = datetime.now(UTC) + timedelta(seconds=2)
    expired = repo_a.expire_stale(now)
    assert expired == [claim.lease_id]

    with queries.connect() as conn:
        lease = conn.execute(
            "select * from executor_leases where id=?", (claim.lease_id,)
        ).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchone()
        run = conn.execute("select * from node_runs where id=?", (claim.node_run_id,)).fetchone()
    assert lease["status"] == "expired"
    assert node["status"] == "failed"
    assert node["error_message"] == "lease expired"
    assert node["stale_reason"] == ""
    assert run["status"] == "failed"


def test_active_counts_reflects_released_leases(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-counts", "local-default", workspace_limit=2
    )
    executor_id = "local-default"
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id=executor_id, global_capacity=2)
    )
    assert claim is not None

    counts = repo_a.active_counts(executor_id)
    assert counts["global"] == 1
    assert counts[workspace_id] == 1

    repo_a.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
    counts_after = repo_a.active_counts(executor_id)
    assert counts_after["global"] == 0
    assert counts_after[workspace_id] == 0


def test_claim_rejected_when_job_paused(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-paused", "local-default", workspace_limit=2
    )
    queries.pause_job(job_id, "awaiting_resources")

    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            execution_mode="until_node",
            target_node_key="review_keywords",
            allowed_node_keys=("review_keywords",),
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_rejected_when_target_snapshot_stale(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-stale",
        "local-default",
        workspace_limit=2,
        node_keys=["review_keywords", "clean_and_parse"],
    )
    queries.set_job_execution_target(job_id, "review_keywords")

    # Snapshot computed before the user changed the target.
    stale_request = _claim_request(
        workspace_id,
        job_id,
        execution_mode="until_node",
        target_node_key="review_keywords",
        allowed_node_keys=("review_keywords",),
    )

    queries.set_job_execution_target(job_id, "clean_and_parse")

    claim = repo_a.try_claim(stale_request)
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_with_stale_full_snapshot_is_rejected_when_job_is_run_to(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-full-ignore", "local-default", workspace_limit=2
    )
    queries.set_job_execution_target(job_id, "review_keywords")

    # The worker read full mode before the user switched the job to run-to.
    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            execution_mode="full",
            target_node_key="other",
            allowed_node_keys=("review_keywords",),
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_sqlite_timestamp_is_utc_without_t_separator() -> None:
    now = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert _sqlite_timestamp(now) == "2025-01-02 03:04:05.123456"


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
        workflow_key="reading_analysis",
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
        workflow_key="reading_analysis",
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
                "reading_analysis",
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


def test_claim_lease_persists_relative_log_path(queries: JobQueries) -> None:
    data_dir = queries.path.parent
    log_path = data_dir / "logs" / "jobs" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-log", "local-default", workspace_limit=2
    )
    request = _claim_request(
        workspace_id,
        job_id,
        executor_id="local-default",
        global_capacity=2,
        log_path=str(log_path),
    )

    with queries.connect() as conn:
        claimed = claim_lease(conn, request, data_dir)
        conn.commit()

    assert claimed is not None
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/jobs/run.log"


def test_finish_lease_persists_relative_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-finish", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.path.parent
    absolute_log = data_dir / "logs" / "updated.log"
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        log_path=str(absolute_log),
    )
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=?", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/updated.log"


def test_fail_without_lease_persists_relative_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-fail", "local-default", workspace_limit=2
    )
    data_dir = queries.path.parent
    absolute_log = data_dir / "logs" / "error.log"
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        log_path=str(absolute_log),
    )
    run_id = repo_a.fail_without_lease(request, "missing binding")
    assert run_id is not None

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=?", (run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/error.log"


def test_try_claim_returns_absolute_log_path_and_persists_relative(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-repo-claim-abs", "local-default", workspace_limit=2
    )
    data_dir = queries.path.parent
    absolute_log = data_dir / "logs" / "jobs" / "run.log"
    absolute_log.parent.mkdir(parents=True, exist_ok=True)
    request = _claim_request(
        workspace_id,
        job_id,
        executor_id="local-default",
        global_capacity=2,
        log_path=str(absolute_log),
    )

    claim = repo_a.try_claim(request)

    assert claim is not None
    assert claim.log_path == str(absolute_log)
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        ).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/jobs/run.log"


def test_finish_lease_canonicalizes_fallback_legacy_absolute_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-finish-fallback", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.path.parent
    legacy_absolute_log = data_dir / "logs" / "legacy.log"
    legacy_absolute_log.parent.mkdir(parents=True, exist_ok=True)
    with queries.connect() as conn:
        conn.execute(
            "update node_runs set log_path=? where id=?",
            (str(legacy_absolute_log), claim.node_run_id),
        )
        conn.execute("commit")

    result = ExecutionResult(status="completed", exit_code=0, log_path="")
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=?", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/legacy.log"


def test_finish_lease_persists_relative_session_dir(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-session", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.path.parent
    absolute_session = (
        data_dir / "jobs" / workspace_id / job_id / "runs" / "node" / "abc" / "session"
    )
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        session_dir=str(absolute_session),
    )
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=?", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["session_dir"] == absolute_session.relative_to(data_dir).as_posix()


def test_finish_lease_rejects_session_dir_outside_jobs(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-invalid-session", "local-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="local-default", global_capacity=2)
    )
    assert claim is not None

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        session_dir="sessions/abc",
    )

    with pytest.raises(ManagedPathError, match="expected 'jobs'"):
        repo_a.finish(claim.lease_id, result)
