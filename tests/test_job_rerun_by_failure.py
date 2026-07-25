from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_catalog import WorkflowCatalogService

NODE_KEYS = [
    "fetch_questions",
    "clean_and_parse",
    "generate_key_info",
    "review_key_info",
]


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path),
        settings,
        WorkflowCatalogService(settings),
        JobArtifactMutationService(settings.jobs_dir),
    )


@pytest.fixture
def workspace(job_db):
    return job_db.create_workspace("default", default_workflow_key="question_comprehension_info")


def _create_job(job_db, workspace, source_id: str) -> dict[str, Any]:
    return job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id=source_id,
        batch_id="",
        title=f"Question {source_id}",
        node_keys=list(NODE_KEYS),
        workspace_id=workspace["id"],
    )


def _fail_node(
    job_db,
    job: dict[str, Any],
    node_key: str,
    category: str,
    detail: str,
    error_message: str = "boom",
) -> None:
    run = job_db.start_node_run(job["id"], node_key, ["cmd"], f"logs/{job['id']}-{node_key}.log")
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            """
            update node_runs
            set status='failed', error_message=?, failure_category=?, failure_detail=?,
                finished_at=current_timestamp
            where id=?
            """,
            (error_message, category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message=? where job_id=? and node_key=?",
            (error_message, job["id"], node_key),
        )
        conn.execute("update jobs set status='failed' where id=?", (job["id"],))
        conn.execute("commit")


def _node_statuses(job_db, job_id: str) -> dict[str, str]:
    return {n["node_key"]: n["status"] for n in job_db.list_job_nodes(job_id)}


def test_filters_by_failure_category(rerun_service, job_db, workspace):
    technical_job = _create_job(job_db, workspace, "Q-tech")
    business_job = _create_job(job_db, workspace, "Q-biz")
    _fail_node(job_db, technical_job, "clean_and_parse", "technical", "provider_stream")
    _fail_node(job_db, business_job, "review_key_info", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert [r["job_id"] for r in results] == [technical_job["id"]]
    assert results[0]["status"] == "succeeded"
    assert _node_statuses(job_db, technical_job["id"])["clean_and_parse"] == "pending"
    assert _node_statuses(job_db, business_job["id"])["review_key_info"] == "failed"


def test_rerun_self_marks_node_pending_and_downstream_stale(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-self")
    _fail_node(job_db, job, "clean_and_parse", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["clean_and_parse"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["clean_and_parse"] == "pending"
    assert nodes["generate_key_info"] == "stale"
    assert nodes["review_key_info"] == "stale"


def test_business_auto_reruns_direct_upstream(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-upstream")
    _fail_node(job_db, job, "review_key_info", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "business")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["generate_key_info"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["generate_key_info"] == "pending"
    assert nodes["review_key_info"] == "stale"


def test_explicit_rerun_self_strategy_on_business_category(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-strategy")
    _fail_node(job_db, job, "review_key_info", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "business", strategy="rerun_self"
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["review_key_info"]


def test_unknown_category_defaults_to_rerun_self(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-unknown")
    _fail_node(job_db, job, "generate_key_info", "unknown", "unknown")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "unknown")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["generate_key_info"]


def test_busy_job_is_skipped(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-busy")
    _fail_node(job_db, job, "fetch_questions", "technical", "provider_stream")
    job_db.update_job_node(job["id"], "clean_and_parse", status="running")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "skipped"
    assert results[0]["reason_code"] == "busy"
    assert results[0]["rerun_nodes"] == []
    assert _node_statuses(job_db, job["id"])["fetch_questions"] == "failed"


def test_job_ids_filter_and_no_match_reporting(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-listed")
    other = _create_job(job_db, workspace, "Q-unlisted")
    _fail_node(job_db, job, "clean_and_parse", "technical", "timeout")
    _fail_node(job_db, other, "clean_and_parse", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", job_ids=[job["id"], "missing-job"]
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id[job["id"]]["status"] == "succeeded"
    assert by_id["missing-job"]["status"] == "skipped"
    assert by_id["missing-job"]["reason_code"] == "no_matching_failure"
    assert other["id"] not in by_id
    assert _node_statuses(job_db, other["id"])["clean_and_parse"] == "failed"


def test_workflow_key_filter_excludes_other_workflows(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-wf")
    _fail_node(job_db, job, "clean_and_parse", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", workflow_key="some_other_workflow"
    )

    assert results == []
    assert _node_statuses(job_db, job["id"])["clean_and_parse"] == "failed"


def test_recovered_node_is_not_rerun_again(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-recovered")
    _fail_node(job_db, job, "clean_and_parse", "technical", "timeout")
    # A newer completed run supersedes the failure for that node.
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, finished_at)
            values (?, 'clean_and_parse', 'completed', current_timestamp, current_timestamp)
            """,
            (job["id"],),
        )
        conn.execute("commit")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results == []
