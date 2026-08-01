from __future__ import annotations

from datetime import UTC, datetime

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import _claim_request, _setup_workspace


def _finish_failed_run(
    repo: ExecutorLeaseRepository,
    queries: JobQueries,
    name: str,
    result: ExecutionResult,
) -> tuple[str, str]:
    workspace_id, job_id = _setup_workspace(queries, name, f"exec-{name}", 1)
    claim = repo.try_claim(_claim_request(workspace_id, job_id, executor_id=f"exec-{name}"))
    assert claim is not None
    assert repo.finish(claim.lease_id, result)
    return workspace_id, job_id


def test_finish_classifies_failed_run_and_mirrors_job_node(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    _, job_id = _finish_failed_run(
        repo_a,
        queries,
        "fc-stream",
        ExecutionResult(status="failed", exit_code=1, error_message="terminated"),
    )

    run = queries.list_node_runs(job_id)[-1]
    assert run["failure_category"] == "technical"
    assert run["failure_detail"] == "provider_stream"
    node = queries.get_job_node(job_id, "review_keywords")
    assert node is not None
    assert node["failure_category"] == "technical"
    assert node["failure_detail"] == "provider_stream"


def test_finish_prefers_declared_classification(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    _, job_id = _finish_failed_run(
        repo_a,
        queries,
        "fc-declared",
        ExecutionResult(
            status="failed",
            exit_code=1,
            error_message="some wrapper text",
            failure_category="business",
            failure_detail="review_rejected",
        ),
    )

    run = queries.list_node_runs(job_id)[-1]
    assert run["failure_category"] == "business"
    assert run["failure_detail"] == "review_rejected"


def test_finish_completed_run_stores_empty_classification(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    _, job_id = _finish_failed_run(
        repo_a,
        queries,
        "fc-completed",
        ExecutionResult(status="completed", exit_code=0),
    )

    run = queries.list_node_runs(job_id)[-1]
    assert run["failure_category"] == ""
    assert run["failure_detail"] == ""


def test_fail_without_lease_classifies_configuration_failure(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-config", "exec-fc-config", 1)
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="question_comprehension_info",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/config.log",
    )

    run_id = repo_a.fail_without_lease(request, "CMS token expired")

    assert run_id is not None
    run = queries.get_node_run(job_id, run_id)
    assert run is not None
    assert run["failure_category"] == "technical"
    assert run["failure_detail"] == "cms_auth"
    node = queries.get_job_node(job_id, "review_keywords")
    assert node is not None
    assert node["failure_category"] == "technical"
    assert node["failure_detail"] == "cms_auth"


def test_orphan_recovery_assigns_worker_orphaned(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-orphan", "exec-fc-orphan", 1)
    del workspace_id
    with queries.connect() as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id=? and node_key=?",
            (job_id, "review_keywords"),
        )
        conn.execute("update jobs set status='running' where id=?", (job_id,))
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (?, ?, 'running', ?, ?)
            """,
            (job_id, "review_keywords", datetime.now(UTC), "logs/orphan.log"),
        )
        conn.execute("commit")

    recovered = repo_a.recover_orphaned_running_jobs(datetime.now(UTC))

    assert recovered == [job_id]
    run = queries.list_node_runs(job_id)[-1]
    assert run["error_message"] == "orphaned recovery"
    assert run["failure_category"] == "technical"
    assert run["failure_detail"] == "worker_orphaned"


_POOL_TIMEOUT_MESSAGE = "PoolTimeout: couldn't get a connection after 10.00 sec"


def test_transient_db_pool_failure_retries_then_fails(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-pool", "exec-fc-pool", 1)
    result = ExecutionResult(status="failed", exit_code=1, error_message=_POOL_TIMEOUT_MESSAGE)

    # First two failures hand the node back to the claimable set.
    for _ in range(2):
        claim = repo_a.try_claim(_claim_request(workspace_id, job_id, executor_id="exec-fc-pool"))
        assert claim is not None
        assert repo_a.finish(claim.lease_id, result)
        node = queries.get_job_node(job_id, "review_keywords")
        assert node is not None
        assert node["status"] == "pending"
        assert node["failure_category"] == "technical"
        assert node["failure_detail"] == "db_pool_timeout"
        job = queries.get_job(job_id)
        assert job is not None
        assert job["status"] != "failed"

    # Third failure is permanent: node and job both fail.
    claim = repo_a.try_claim(_claim_request(workspace_id, job_id, executor_id="exec-fc-pool"))
    assert claim is not None
    assert repo_a.finish(claim.lease_id, result)
    node = queries.get_job_node(job_id, "review_keywords")
    assert node is not None
    assert node["status"] == "failed"
    job = queries.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"

    # Every attempt stays recorded as its own failed run.
    runs = [run for run in queries.list_node_runs(job_id) if run["status"] == "failed"]
    assert len(runs) == 3
    assert all(run["failure_detail"] == "db_pool_timeout" for run in runs)
