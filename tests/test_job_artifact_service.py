from pathlib import Path

import pytest

from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import InvalidOperationError, NotFoundError


@pytest.fixture
def artifact_service(job_db):
    return JobArtifactService(job_db)


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace("default")
    batch = job_db.create_batch(
        "question_content", "direct_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    return job_db.create_job(
        workflow_key="question_content",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding"],
        workspace_id=workspace["id"],
    )


def test_job_artifact_service_reads_file(artifact_service, job):
    storage = Path(job["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")

    result = artifact_service.read(job["id"], "result.json")

    assert result["name"] == "result.json"
    assert result["content"] == '{"ok": true}'


def test_job_artifact_service_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.read(job["id"], "../video_hive.sqlite")


def test_job_artifact_service_missing_job(artifact_service):
    with pytest.raises(NotFoundError, match="Job not found"):
        artifact_service.read("missing", "result.json")


def test_job_artifact_service_reject_subpath(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid job path"):
        artifact_service.reject_subpath(job["id"])
