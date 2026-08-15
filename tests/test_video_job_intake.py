import json
from pathlib import Path

import pytest

from server.app.jobs.storage_layout import job_storage_dir
from server.app.main import create_app
from server.app.services.job_intake import JobIntakeService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return app


def make_job_intake_service(app):
    return JobIntakeService(
        app.state.job_db,
        app.state.settings,
        WorkflowCatalogService(app.state.settings),
        job_event_manager=app.state.job_event_manager,
    )


def _create_workspace_with_revision(app, workflow_key="video_knowledge"):
    workspace = app.state.job_db.create_workspace(
        "video_knowledge", default_workflow_key=workflow_key, default_entity="video"
    )
    definition = WorkflowCatalogService(app.state.settings).definition(workflow_key)
    WorkflowRevisionService(app.state.job_db).ensure_active_revision(workspace["id"], definition)
    return workspace


def test_video_url_intake_creates_video_job_without_prefetching_input(app) -> None:
    """Intake is entity-agnostic (plan §1.4 #31): it no longer pre-writes
    video_input.json; the download node builds it from the batch candidate."""
    _create_workspace_with_revision(app)
    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_urls",
        "entity": "video",
        "video_urls": ["https://example.invalid/video.mp4"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 1
    job = result["jobs"][0]
    assert job["source_type"] == "video"
    job_dir = job_storage_dir(app.state.settings.jobs_dir, job["workspace_id"], job["id"])
    assert not (job_dir / "video_input.json").exists()


def test_video_external_id_duplicate_is_rejected_or_reported(app) -> None:
    _create_workspace_with_revision(app)

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001"],
    }

    first = service.create_batch("video_knowledge", payload)
    second = service.create_batch("video_knowledge", payload)

    assert first["created_count"] == 1
    assert second["created_count"] == 0


def test_video_knowledge_intake_does_not_call_cms(app, monkeypatch) -> None:
    """Knowledge-mode intake is a pure fan-out; CMS resolution moved to the
    download DAG node, so intake must not touch the knowledge lookup."""
    _create_workspace_with_revision(app)

    def fail_on_cms(*args, **kwargs):
        raise AssertionError("intake must not call the CMS")

    monkeypatch.setattr("workspace_libs.cms.knowledge.lookup_knowledge_video", fail_on_cms)

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001", "K002"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 2
    assert [job["source_id"] for job in result["jobs"]] == ["K001", "K002"]


def test_video_knowledge_intake_keeps_source_ref_opaque_in_candidate(app) -> None:
    """Knowledge-mode intake fans out opaque candidates: no video_input.json
    is written and no CMS fields are resolved at intake time; the download
    node fills source_url/source_uuid/title from the CMS at execution."""
    _create_workspace_with_revision(app)

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 1
    job = result["jobs"][0]
    job_dir = job_storage_dir(app.state.settings.jobs_dir, job["workspace_id"], job["id"])
    assert not (job_dir / "video_input.json").exists()
    source_payload = json.loads(result["batch"]["source_payload_json"])
    (candidate,) = source_payload["task_candidates"]
    assert candidate["source_ref"] == "K001"
    assert candidate["source_url"] == ""
    assert candidate["source_uuid"] == ""
    assert "token" not in json.dumps(candidate)


def test_video_knowledge_intake_dedupes_repeated_codes_across_chunks(app, monkeypatch) -> None:
    _create_workspace_with_revision(app)
    monkeypatch.setattr("server.app.services.job_intake_chunks.INTAKE_RESOLUTION_CHUNK_SIZE", 1)

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001", "K002"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 2
    assert [job["source_id"] for job in result["jobs"]] == ["K001", "K002"]
