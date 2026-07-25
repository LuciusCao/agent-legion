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
