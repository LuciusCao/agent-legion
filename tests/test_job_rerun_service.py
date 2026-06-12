from pathlib import Path

import pytest

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_rerun import JobRerunService
from server.app.services.pipeline_catalog import PipelineCatalogService


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(job_db, settings, PipelineCatalogService(settings))


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    return job_db.create_job(
        pipeline_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )


@pytest.fixture
def running_job(job_db):
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
        node_keys=["question_understanding"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "question_understanding", status="running")
    return job


def test_job_rerun_service_rejects_running_job(rerun_service, running_job):
    with pytest.raises(InvalidOperationError, match="Cannot rerun a running job"):
        rerun_service.rerun_node(running_job["id"], "question_understanding")


def test_job_rerun_service_reruns_node(rerun_service, job):
    result = rerun_service.rerun_node(job["id"], "question_understanding")
    assert result["job_id"] == job["id"]
    assert result["node_key"] == "question_understanding"


def test_job_delete_removes_storage_and_logs(rerun_service, job, settings):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    log = settings.logs_dir / "jobs" / f"{job['id']}-node.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("log", encoding="utf-8")

    rerun_service.delete(job["id"])

    assert not storage.exists()
    assert not log.exists()


def test_job_delete_rejects_missing_job(rerun_service):
    with pytest.raises(NotFoundError, match="Job not found"):
        rerun_service.delete("missing")
