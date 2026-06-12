import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.pipeline_catalog import PipelineCatalogService


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(job_db, settings, PipelineCatalogService(settings))


def test_job_query_service_lists_jobs(query_service, job_db):
    job_db.create_workspace("default")
    job = query_service.list_jobs("default")
    assert isinstance(job, list)


def test_job_query_service_detail_enriches_nodes(query_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )

    detail = query_service.detail(job["id"])

    assert detail["job"]["id"] == job["id"]
    assert len(detail["nodes"]) == 2
    assert detail["nodes"][0]["label"]
    assert "artifacts" in detail


def test_workspace_run_service_filters_runs(query_service, job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["assemble_package"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "assemble_package", status="failed")

    result = query_service.workspace_runs(
        workspace["id"], status="failed", node_key="assemble_package", job_id=None, limit=25
    )
    assert all(run["status"] == "failed" for run in result)


def test_workspace_dag_preserves_status_buckets(query_service, job_db):
    workspace = job_db.create_workspace("default")
    job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )

    payload = query_service.workspace_dag(workspace["id"])
    assert payload["nodes"][0]["status_counts"].keys() == {
        "pending",
        "running",
        "completed",
        "failed",
        "stale",
    }
