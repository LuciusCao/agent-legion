from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_rerun import JobRerunService
from tests.helpers import publish_builtin_revision

NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
    )


@pytest.fixture
def workspace(job_db):
    created = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, created["id"])
    return created


def _create_job(job_db, workspace, source_id: str) -> dict[str, Any]:
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id="",
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
            set status='failed', error_message=%s, failure_category=%s, failure_detail=%s,
                finished_at=current_timestamp
            where id=%s
            """,
            (error_message, category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message=%s where job_id=%s and node_key=%s",
            (error_message, job["id"], node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job["id"],))
        conn.execute("commit")


def _node_statuses(job_db, job_id: str) -> dict[str, str]:
    return {n["node_key"]: n["status"] for n in job_db.list_job_nodes(job_id)}


def test_filters_by_failure_category(rerun_service, job_db, workspace):
    technical_job = _create_job(job_db, workspace, "Q-tech")
    business_job = _create_job(job_db, workspace, "Q-biz")
    _fail_node(job_db, technical_job, "write_script", "technical", "provider_stream")
    _fail_node(job_db, business_job, "publish_content", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert [r["job_id"] for r in results] == [technical_job["id"]]
    assert results[0]["status"] == "succeeded"
    assert _node_statuses(job_db, technical_job["id"])["write_script"] == "pending"
    assert _node_statuses(job_db, business_job["id"])["publish_content"] == "failed"


def test_rerun_self_marks_node_pending_and_downstream_stale(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-self")
    _fail_node(job_db, job, "write_script", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["write_script"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"
    assert nodes["publish_content"] == "stale"


def test_business_auto_reruns_direct_upstream(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-upstream")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "business")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["write_script"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"


def test_explicit_rerun_self_strategy_on_business_category(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-strategy")
    _fail_node(job_db, job, "publish_content", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "business", strategy="rerun_self"
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["publish_content"]


def test_unknown_category_defaults_to_rerun_self(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-unknown")
    _fail_node(job_db, job, "review_script", "unknown", "unknown")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "unknown")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["review_script"]


def test_busy_job_is_skipped(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-busy")
    _fail_node(job_db, job, "intake_knowledge_points", "technical", "provider_stream")
    job_db.update_job_node(job["id"], "write_script", status="running")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "skipped"
    assert results[0]["reason_code"] == "busy"
    assert results[0]["rerun_nodes"] == []
    assert _node_statuses(job_db, job["id"])["intake_knowledge_points"] == "failed"


def test_job_ids_filter_and_no_match_reporting(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-listed")
    other = _create_job(job_db, workspace, "Q-unlisted")
    _fail_node(job_db, job, "write_script", "technical", "timeout")
    _fail_node(job_db, other, "write_script", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", job_ids=[job["id"], "missing-job"]
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id[job["id"]]["status"] == "succeeded"
    assert by_id["missing-job"]["status"] == "skipped"
    assert by_id["missing-job"]["reason_code"] == "no_matching_failure"
    assert other["id"] not in by_id
    assert _node_statuses(job_db, other["id"])["write_script"] == "failed"


def test_workflow_key_filter_excludes_other_workflows(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-wf")
    _fail_node(job_db, job, "write_script", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", workflow_key="some_other_workflow"
    )

    assert results == []
    assert _node_statuses(job_db, job["id"])["write_script"] == "failed"


def test_recovered_node_is_not_rerun_again(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-recovered")
    _fail_node(job_db, job, "write_script", "technical", "timeout")
    # A newer completed run supersedes the failure for that node.
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, finished_at)
            values (%s, 'write_script', 'completed', current_timestamp, current_timestamp)
            """,
            (job["id"],),
        )
        conn.execute("commit")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results == []


def test_rerun_clears_failure_fields_on_job_nodes(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-clear")
    _fail_node(
        job_db,
        job,
        "write_script",
        "technical",
        "stale_definition",
        error_message=(
            "Agent definition 'generator-v1' was disabled or changed while the request was queued"
        ),
    )
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set failure_category='technical',"
            " failure_detail='stale_definition' where job_id=%s and node_key=%s",
            (job["id"], "write_script"),
        )
        conn.execute("commit")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "succeeded"
    node = job_db.get_job_node(job["id"], "write_script")
    assert node["status"] == "pending"
    assert node["failure_category"] == ""
    assert node["failure_detail"] == ""
