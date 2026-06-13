import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.leases import ExecutorLeaseRepository, _sqlite_timestamp
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
    LeaseClaimRequest,
)
from server.app.jobs.queries import JobQueries


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
    pipeline_key: str = "reading_analysis",
) -> tuple[str, str]:
    workspace = queries.create_workspace(name=name, default_pipeline_key=pipeline_key)
    workspace_id = workspace["id"]
    job_id = _create_job_in_workspace(
        queries, workspace_id, node_key=node_key, pipeline_key=pipeline_key
    )
    _bind_executor_to_node(
        queries,
        workspace_id,
        executor_id,
        workspace_limit,
        node_key=node_key,
        local_limit=local_limit,
        pipeline_key=pipeline_key,
    )
    return workspace_id, job_id


def _create_job_in_workspace(
    queries: JobQueries,
    workspace_id: str,
    node_key: str = "review_keywords",
    pipeline_key: str = "reading_analysis",
) -> str:
    job = queries.create_job(
        pipeline_key=pipeline_key,
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        batch_id="",
        title="Test Job",
        node_keys=[node_key],
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
    pipeline_key: str = "reading_analysis",
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
            insert into workspace_node_bindings(workspace_id, pipeline_key, node_key, executor_id)
            values (?, ?, ?, ?)
            on conflict(workspace_id, pipeline_key, node_key) do update set
              executor_id=excluded.executor_id
            """,
            (workspace_id, pipeline_key, node_key, executor_id),
        )
        if local_limit is not None:
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, pipeline_key, node_key, concurrency_limit)
                values (?, ?, ?, ?)
                on conflict(workspace_id, pipeline_key, node_key) do update set
                  concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, pipeline_key, node_key, local_limit),
            )


def _claim_request(
    workspace_id: str,
    job_id: str,
    node_key: str = "review_keywords",
    executor_id: str = "local-default",
    global_capacity: int = 2,
    local_node_limit: int | None = 1,
    ttl: int = 60,
    log_path: str = "/tmp/run.log",
    pipeline_key: str = "reading_analysis",
    execution_mode: str = "full",
    target_node_key: str | None = None,
    allowed_node_keys: tuple[str, ...] = (),
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=executor_id,
        global_capacity=global_capacity,
        workspace_id=workspace_id,
        job_id=job_id,
        pipeline_key=pipeline_key,
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
            "insert into workspace_node_bindings(workspace_id, pipeline_key, node_key, executor_id) values (?, ?, ?, ?)",
            (workspace_id, "reading_analysis", other_node_key, executor_id),
        )
        conn.execute(
            "insert into workspace_node_limits(workspace_id, pipeline_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
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

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("python", "run.py"),
        log_path="/tmp/updated.log",
        session_reference="/sessions/abc",
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
    assert run["log_path"] == "/tmp/updated.log"
    assert run["session_dir"] == "/sessions/abc"
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
        pipeline_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="/tmp/error.log",
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
        pipeline_key="reading_analysis",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="/tmp/error.log",
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
    workspace_id, job_id = _setup_workspace(queries, "ws-stale", "local-default", workspace_limit=2)
    queries.set_job_execution_target(job_id, "review_keywords")

    # Snapshot computed before the user changed the target.
    stale_request = _claim_request(
        workspace_id,
        job_id,
        execution_mode="until_node",
        target_node_key="review_keywords",
        allowed_node_keys=("review_keywords",),
    )

    queries.set_job_execution_target(job_id, "extract_entities")

    claim = repo_a.try_claim(stale_request)
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_with_full_mode_ignores_target_fields(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-full-ignore", "local-default", workspace_limit=2
    )
    queries.set_job_execution_target(job_id, "review_keywords")

    # full mode should succeed even though target differs from node.
    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            execution_mode="full",
            target_node_key="other",
            allowed_node_keys=("review_keywords",),
        )
    )
    assert isinstance(claim, ClaimedExecution)


def test_sqlite_timestamp_is_utc_without_t_separator() -> None:
    now = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert _sqlite_timestamp(now) == "2025-01-02 03:04:05.123456"
