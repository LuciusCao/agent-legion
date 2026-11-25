from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.jobs import JobQueries
from server.app.services.workflow_revision_format import serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
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


def test_finish_classifies_http_200_provider_auth_error(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _finish_failed_run(
        repo_a,
        queries,
        "fc-provider-auth",
        ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=(
                "provider call failed: HTTP 200 error body: code=401: "
                "Authorization apikey is disabled"
            ),
        ),
    )

    run = queries.list_node_runs(job_id)[-1]
    assert run["failure_category"] == "technical"
    assert run["failure_detail"] == "provider_request"
    node = queries.get_job_node(job_id, "review_keywords")
    assert node is not None
    assert node["failure_category"] == "technical"
    failed = queries.list_failed_node_runs(workspace_id, category="technical")
    assert [item["job_id"] for item in failed] == [job_id]


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
        workflow_key="demo_workflow",
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
            "update job_nodes set status='running' where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        )
        conn.execute("update jobs set status='running' where id=%s", (job_id,))
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, log_path)
            values (%s, %s, 'running', %s, %s)
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


def _freeze_failure_default(queries: JobQueries, job_id: str, category: str):
    definition = workflow_definition_from_mapping(
        {
            "key": "demo_workflow",
            "label": "Demo",
            "nodes": {
                "review_keywords": {
                    "type": "code",
                    "capability": "review_keywords",
                    "failure_default": category,
                }
            },
        }
    )
    with queries.connect() as conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json=%s where id=%s",
            (serialize_definition(definition), job_id),
        )
        conn.execute("commit")
    return definition


def _publish_active_failure_default(queries: JobQueries, workspace_id: str, frozen, category: str):
    nodes = dict(frozen.nodes)
    nodes["review_keywords"] = replace(nodes["review_keywords"], failure_default=category)
    return WorkflowRevisionService(queries).ensure_active_revision(
        workspace_id, replace(frozen, nodes=nodes)
    )


def test_finish_uses_frozen_failure_default_only_for_unknown_and_exposes_business_query(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-default-business", "exec-default", 1)
    frozen = _freeze_failure_default(queries, job_id, "business")
    _publish_active_failure_default(queries, workspace_id, frozen, "technical")
    claim = repo_a.try_claim(_claim_request(workspace_id, job_id, executor_id="exec-default"))
    assert claim is not None

    assert repo_a.finish(
        claim.lease_id,
        ExecutionResult(status="failed", exit_code=1, error_message="unrecognized content"),
    )

    run = queries.list_node_runs(job_id)[-1]
    assert (run["failure_category"], run["failure_detail"]) == ("business", "unknown")
    assert [
        row["job_id"] for row in queries.list_failed_node_runs(workspace_id, category="business")
    ] == [job_id]


def test_finish_known_rule_and_declared_fact_beat_frozen_failure_default(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-default-priority", "exec-priority", 1)
    _freeze_failure_default(queries, job_id, "business")
    claim = repo_a.try_claim(_claim_request(workspace_id, job_id, executor_id="exec-priority"))
    assert claim is not None
    assert repo_a.finish(
        claim.lease_id,
        ExecutionResult(status="failed", exit_code=1, error_message="terminated"),
    )
    run = queries.list_node_runs(job_id)[-1]
    assert (run["failure_category"], run["failure_detail"]) == ("technical", "provider_stream")

    declared_workspace, declared_job = _setup_workspace(
        queries, "fc-default-declared", "exec-declared", 1
    )
    _freeze_failure_default(queries, declared_job, "business")
    rerun = repo_a.try_claim(
        _claim_request(declared_workspace, declared_job, executor_id="exec-declared")
    )
    assert rerun is not None
    assert repo_a.finish(
        rerun.lease_id,
        ExecutionResult(
            status="failed",
            exit_code=1,
            error_message="unrecognized content",
            failure_category="unknown",
            failure_detail="system_observed_unknown",
        ),
    )
    declared = queries.list_node_runs(declared_job)[-1]
    assert (declared["failure_category"], declared["failure_detail"]) == (
        "unknown",
        "system_observed_unknown",
    )


def test_fail_without_lease_uses_frozen_failure_default_for_unknown(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-config-default", "exec-config-default", 1)
    frozen = _freeze_failure_default(queries, job_id, "business")
    _publish_active_failure_default(queries, workspace_id, frozen, "technical")
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/config.log",
    )

    run_id = repo_a.fail_without_lease(request, "unrecognized configuration outcome")

    assert run_id is not None
    run = queries.get_node_run(job_id, run_id)
    assert run is not None
    assert (run["failure_category"], run["failure_detail"]) == ("business", "unknown")


def test_fail_without_lease_known_rule_beats_frozen_failure_default(
    repo_a: ExecutorLeaseRepository, queries: JobQueries
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fc-config-known", "exec-config-known", 1)
    _freeze_failure_default(queries, job_id, "business")
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
        node_key="review_keywords",
        capability="review_keywords",
        log_path="logs/config.log",
    )

    run_id = repo_a.fail_without_lease(request, "CMS token expired")

    assert run_id is not None
    run = queries.get_node_run(job_id, run_id)
    assert run is not None
    assert (run["failure_category"], run["failure_detail"]) == ("technical", "cms_auth")
