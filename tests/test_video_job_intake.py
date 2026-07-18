from pathlib import Path

import pytest

from server.app.cms.client import CmsVideoLookup
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


def test_video_url_intake_creates_video_job_and_input_artifact(app) -> None:
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
    job_dir = app.state.settings.jobs_dir / job["workspace_id"] / job["id"]
    assert (job_dir / "video_input.json").is_file()


def test_video_external_id_duplicate_is_rejected_or_reported(app, monkeypatch) -> None:
    _create_workspace_with_revision(app)

    def fake_lookup_knowledge_video(code, api_url=None, token=None):
        return CmsVideoLookup(
            status="found",
            url="https://example.invalid/knowledge.mp4",
            title=f"Knowledge {code}",
            source_uuid="uuid-1",
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_video.lookup_knowledge_video",
        fake_lookup_knowledge_video,
    )
    monkeypatch.setattr("server.app.services.job_intake_video.get_token", lambda env, config: "")

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


def test_video_knowledge_intake_dedupes_shared_video_url(app, monkeypatch) -> None:
    _create_workspace_with_revision(app)

    def fake_lookup_knowledge_video(code, api_url=None, token=None):
        return CmsVideoLookup(
            status="found",
            url="https://example.invalid/shared.mp4",
            title=f"Knowledge {code}",
            source_uuid="uuid-shared",
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_video.lookup_knowledge_video",
        fake_lookup_knowledge_video,
    )
    monkeypatch.setattr("server.app.services.job_intake_video.get_token", lambda env, config: "")

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001", "K002"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 1
    assert result["jobs"][0]["source_id"] == "K001"


def test_video_knowledge_intake_keeps_distinct_video_urls(app, monkeypatch) -> None:
    _create_workspace_with_revision(app)

    def fake_lookup_knowledge_video(code, api_url=None, token=None):
        return CmsVideoLookup(
            status="found",
            url=f"https://example.invalid/{code}.mp4",
            title=f"Knowledge {code}",
            source_uuid=f"uuid-{code}",
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_video.lookup_knowledge_video",
        fake_lookup_knowledge_video,
    )
    monkeypatch.setattr("server.app.services.job_intake_video.get_token", lambda env, config: "")

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001", "K002"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 2


def test_video_knowledge_intake_dedupes_shared_url_across_chunks(app, monkeypatch) -> None:
    _create_workspace_with_revision(app)

    def fake_lookup_knowledge_video(code, api_url=None, token=None):
        return CmsVideoLookup(
            status="found",
            url="https://example.invalid/shared.mp4",
            title=f"Knowledge {code}",
            source_uuid="uuid-shared",
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_video.lookup_knowledge_video",
        fake_lookup_knowledge_video,
    )
    monkeypatch.setattr("server.app.services.job_intake_video.get_token", lambda env, config: "")
    monkeypatch.setattr("server.app.services.job_intake_chunks.INTAKE_RESOLUTION_CHUNK_SIZE", 1)

    service = make_job_intake_service(app)
    payload = {
        "workflow_key": "video_knowledge",
        "source_kind": "batch_by_knowledge",
        "entity": "video",
        "knowledge_codes": ["K001", "K002"],
    }

    result = service.create_batch("video_knowledge", payload)

    assert result["created_count"] == 1
    assert result["jobs"][0]["source_id"] == "K001"
