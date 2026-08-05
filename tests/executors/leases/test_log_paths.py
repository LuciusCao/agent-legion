from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.app.executors._lease_claims import claim_lease
from server.app.executors.leases import ExecutorLeaseRepository, database_timestamp
from server.app.executors.models import (
    ConfigurationFailureRequest,
    ExecutionResult,
)
from server.app.jobs import JobQueries
from server.app.storage_paths import ManagedPathError
from tests.executors.leases.helpers import (
    _claim_request,
    _setup_workspace,
)


def test_claim_lease_persists_relative_log_path(queries: JobQueries) -> None:
    data_dir = queries.jobs_dir.parent
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
            "select * from node_runs where job_id=%s and node_key=%s",
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

    data_dir = queries.jobs_dir.parent
    absolute_log = data_dir / "logs" / "updated.log"
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        log_path=str(absolute_log),
    )
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/updated.log"


def test_fail_without_lease_persists_relative_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-fail", "local-default", workspace_limit=2
    )
    data_dir = queries.jobs_dir.parent
    absolute_log = data_dir / "logs" / "error.log"
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="question_comprehension_info",
        node_key="review_keywords",
        capability="review_keywords",
        log_path=str(absolute_log),
    )
    run_id = repo_a.fail_without_lease(request, "missing binding")
    assert run_id is not None

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/error.log"


def test_try_claim_returns_absolute_log_path_and_persists_relative(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-repo-claim-abs", "local-default", workspace_limit=2
    )
    data_dir = queries.jobs_dir.parent
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
            "select * from node_runs where job_id=%s and node_key=%s",
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

    data_dir = queries.jobs_dir.parent
    legacy_absolute_log = data_dir / "logs" / "legacy.log"
    legacy_absolute_log.parent.mkdir(parents=True, exist_ok=True)
    with queries.connect() as conn:
        conn.execute(
            "update node_runs set log_path=%s where id=%s",
            (str(legacy_absolute_log), claim.node_run_id),
        )
        conn.execute("commit")

    result = ExecutionResult(status="completed", exit_code=0, log_path="")
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
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

    data_dir = queries.jobs_dir.parent
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
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
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


def test_database_timestamp_is_utc_without_t_separator() -> None:
    now = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert database_timestamp(now) == "2025-01-02 03:04:05.123456"
