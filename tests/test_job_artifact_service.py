import pytest

from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir


@pytest.fixture
def artifact_service(job_db):
    return JobArtifactService(job_db)


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    return job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding"],
        workspace_id=workspace["id"],
    )


def test_job_artifact_service_reads_file(artifact_service, job, job_db):
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")

    result = artifact_service.read(job["id"], "result.json")

    assert result["name"] == "result.json"
    assert result["content"] == '{"ok": true}'


def test_job_artifact_service_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.read(job["id"], "../agent_legion.sqlite")


def test_job_artifact_service_missing_job(artifact_service):
    with pytest.raises(NotFoundError, match="Job not found"):
        artifact_service.read("missing", "result.json")


def test_job_artifact_service_reject_subpath(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid job path"):
        artifact_service.reject_subpath(job["id"])
