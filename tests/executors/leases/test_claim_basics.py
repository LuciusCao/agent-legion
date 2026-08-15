from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import (
    ClaimedExecution,
    ConfigurationFailureRequest,
    ExecutionResult,
)
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import (
    _claim_request,
    _create_job_in_workspace,
    _setup_workspace,
)


def test_two_workers_claim_one_node_only_one_succeeds(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-one", "code-default", workspace_limit=2)
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
            "select * from node_runs where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchall()
        leases = conn.execute(
            "select * from executor_leases where job_id=%s and node_key=%s and status='active'",
            (job_id, "review_keywords"),
        ).fetchall()
    assert len(runs) == 1
    assert len(leases) == 1


def test_global_capacity_blocks_third_claim(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id_a = _setup_workspace(
        queries, "ws-global", "code-default", workspace_limit=2
    )
    workspace_id_b, job_id_b = _setup_workspace(
        queries, "ws-global-b", "code-default", workspace_limit=2
    )
    executor_id = "code-default"
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
        queries, "ws-limit", "code-default", workspace_limit=1
    )
    job_id_b = _create_job_in_workspace(queries, workspace_id)

    executor_id = "code-default"
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


def test_after_releasing_one_a_lease_b_can_claim(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "code-default"
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


def test_failed_claim_does_not_persist_any_state(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-fail", "code-default", workspace_limit=1)
    repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=1)
    )
    failed = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=1)
    )
    assert failed is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=%s", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=%s", (job_id,)).fetchall()
        nodes = conn.execute(
            "select * from job_nodes where job_id=%s and status='running'", (job_id,)
        ).fetchall()
    assert len(runs) == 1
    assert len(leases) == 1
    assert len(nodes) == 1


def test_finish_is_idempotent_and_updates_job_aggregate_status(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-finish", "code-default", workspace_limit=2)
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    run_dir = data_dir / "jobs" / workspace_id / job_id / "runs" / "review_keywords" / "abc"
    session_dir = run_dir / "session"
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("python", "run.py"),
        log_path="logs/updated.log",
        run_dir=str(run_dir),
        session_dir=str(session_dir),
        skill_version="v1.2.3@abc123",
    )
    assert repo_a.finish(claim.lease_id, result) is True
    assert repo_a.finish(claim.lease_id, result) is False

    with queries.connect() as conn:
        lease = conn.execute(
            "select * from executor_leases where id=%s", (claim.lease_id,)
        ).fetchone()
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
        job = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
    assert lease["status"] == "released"
    assert run["status"] == "completed"
    assert run["exit_code"] == 0
    assert run["log_path"] == "logs/updated.log"
    assert run["run_dir"] == run_dir.relative_to(data_dir).as_posix()
    assert run["session_dir"] == session_dir.relative_to(data_dir).as_posix()
    assert run["skill_version"] == "v1.2.3@abc123"
    assert job["status"] == "completed"


def test_finish_compresses_persisted_pi_event_stream(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-compress", "pi-default", workspace_limit=1)
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="pi-default", global_capacity=1)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    run_dir = data_dir / "jobs" / workspace_id / job_id / "runs" / "review_keywords" / "run-1"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "message_update", "delta": "streaming"},
                {"type": "text_delta", "delta": "partial"},
                {"type": "message_end", "message": {"content": "final"}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = ExecutionResult(status="completed", exit_code=0, run_dir=str(run_dir))
    assert repo_a.finish(claim.lease_id, result) is True

    remaining = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert remaining == [{"type": "message_end", "message": {"content": "final"}}]


def test_fail_without_lease_creates_failed_run_and_updates_job_status(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-fail-no-lease", "code-default", workspace_limit=2
    )
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/error.log",
    )
    run_id = repo_a.fail_without_lease(request, "missing binding")
    assert run_id is not None

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (run_id,)).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchone()
        job = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
    assert run["status"] == "failed"
    assert run["error_message"] == "missing binding"
    assert node["status"] == "failed"
    assert job["status"] == "failed"


def test_fail_without_lease_is_idempotent_for_the_same_node(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-fail-no-lease-idempotent", "code-default", workspace_limit=2
    )
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
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
            "select count(*) from node_runs where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchone()[0]
    assert run_count == 1


def test_heartbeat_extends_lease_expiry(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-heartbeat", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2, ttl=1)
    )
    assert claim is not None

    assert repo_a.heartbeat(claim.lease_id, ttl_seconds=3600) is True
    now = datetime.now(UTC) + timedelta(seconds=2)
    expired = repo_a.expire_stale(now)
    assert claim.lease_id not in expired


def test_heartbeat_returns_false_after_lease_left_active_state(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """A heartbeat racing a finish/expiry must not report success: the UPDATE
    guard leaves the row untouched, and the caller must learn ownership is
    gone instead of assuming the lease was renewed."""
    workspace_id, job_id = _setup_workspace(
        queries, "ws-heartbeat-stale", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2, ttl=1)
    )
    assert claim is not None

    now = datetime.now(UTC) + timedelta(seconds=2)
    assert repo_a.expire_stale(now) == [claim.lease_id]

    assert repo_a.heartbeat(claim.lease_id, ttl_seconds=3600) is False


def test_expire_stale_releases_expired_leases(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-expire", "code-default", workspace_limit=2)
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2, ttl=1)
    )
    assert claim is not None

    now = datetime.now(UTC) + timedelta(seconds=2)
    expired = repo_a.expire_stale(now)
    assert expired == [claim.lease_id]

    with queries.connect() as conn:
        lease = conn.execute(
            "select * from executor_leases where id=%s", (claim.lease_id,)
        ).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchone()
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
    assert lease["status"] == "expired"
    assert node["status"] == "failed"
    assert node["error_message"] == "lease expired"
    assert node["stale_reason"] == ""
    assert run["status"] == "failed"


def test_active_counts_reflects_released_leases(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-counts", "code-default", workspace_limit=2)
    executor_id = "code-default"
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
