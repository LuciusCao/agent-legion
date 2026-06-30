import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_video_job_projection_returns_job_directory_artifacts(client, app, tmp_path: Path) -> None:
    job_db = app.state.job_db
    job_db.create_workspace("video_knowledge", default_workflow_key="video_knowledge")
    job = job_db.create_job(
        workflow_key="video_knowledge",
        source_type="video",
        source_id="K001",
        batch_id="",
        title="Video title",
        node_keys=["download", "transcribe"],
        workspace_id="video_knowledge",
    )
    job_dir = app.state.settings.jobs_dir / job["workspace_id"] / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "legacy_video_id": "legacy-1",
                "external_id": "K001",
                "source_uuid": "",
                "source_url": "https://example.invalid/video.mp4",
                "title": "Video title",
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )
    (job_dir / "source.mp4").write_bytes(b"fake mp4")
    (job_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (job_dir / "interactions.json").write_text("[]", encoding="utf-8")

    response = client.get(f"/api/jobs/{job['id']}/video")

    assert response.status_code == 200
    payload = response.json()
    assert payload["input"]["external_id"] == "K001"
    assert payload["artifacts"]["subtitles"][0]["text"] == "你好"
    assert payload["artifacts"]["chapters"] == []
    assert payload["artifacts"]["interactions"] == []
    assert payload["artifacts"]["video_url"] == f"/api/jobs/{job['id']}/video/source"


def test_video_job_projection_accepts_legacy_interactions_wrapper(client, app) -> None:
    job_db = app.state.job_db
    job_db.create_workspace("video_knowledge", default_workflow_key="video_knowledge")
    job = job_db.create_job(
        workflow_key="video_knowledge",
        source_type="video",
        source_id="K003",
        batch_id="",
        title="Video title",
        node_keys=["download", "transcribe"],
        workspace_id="video_knowledge",
    )
    job_dir = app.state.settings.jobs_dir / job["workspace_id"] / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "legacy_video_id": "legacy-3",
                "external_id": "K003",
                "source_uuid": "",
                "source_url": "https://example.invalid/video.mp4",
                "title": "Video title",
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )
    (job_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "i1", "type": "strict_sequence"}]}),
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job['id']}/video")

    assert response.status_code == 200
    assert response.json()["artifacts"]["interactions"] == [{"id": "i1", "type": "strict_sequence"}]


def test_video_job_source_file_serves_copied_artifact(client, app) -> None:
    job_db = app.state.job_db
    job_db.create_workspace("video_knowledge", default_workflow_key="video_knowledge")
    job = job_db.create_job(
        workflow_key="video_knowledge",
        source_type="video",
        source_id="K002",
        batch_id="",
        title="Video title",
        node_keys=["download"],
        workspace_id="video_knowledge",
    )
    job_dir = app.state.settings.jobs_dir / job["workspace_id"] / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "source.mp4").write_bytes(b"fake mp4")

    response = client.get(f"/api/jobs/{job['id']}/video/source")

    assert response.status_code == 200
    assert response.content == b"fake mp4"


def test_video_job_projection_rejects_non_video_job(client, app) -> None:
    app.state.job_db.create_workspace(
        "question_comprehension", default_workflow_key="question_comprehension_info"
    )
    job = app.state.job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q001",
        batch_id="",
        title="Question",
        node_keys=["fetch_questions"],
        workspace_id="question_comprehension",
    )

    response = client.get(f"/api/jobs/{job['id']}/video")

    assert response.status_code == 404
