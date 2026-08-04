from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from server.app.jobs import JobQueries
from server.app.main import create_app
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.registry import load_registered_workflow
from tests.helpers.auth import authenticate_client


def _create_video_knowledge_job(
    job_db: JobQueries,
    settings: Settings,
    workspace_id: str,
    external_id: str,
    legacy_video_id: str,
) -> dict[str, Any]:
    job_db.create_workspace(workspace_id, default_workflow_key="video_knowledge")
    batch = job_db.create_batch(
        "video_knowledge",
        "batch_by_urls",
        {"video_urls": [f"https://example.com/{external_id}.mp4"]},
        workspace_id,
    )
    definition = load_registered_workflow(settings.root_dir, "video_knowledge")
    job = job_db.create_job(
        workflow_key="video_knowledge",
        source_type="video",
        source_id=external_id,
        batch_id=batch["id"],
        title=f"Video {external_id}",
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
    )
    job_db.update_job_status(job["id"], "completed")

    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "legacy_video_id": legacy_video_id,
                "external_id": external_id,
                "source_uuid": "uuid-1",
                "source_url": f"https://example.com/{external_id}.mp4",
                "title": f"Video {external_id}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job


def test_get_video_job_source_serves_local_source_mp4(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    job = _create_video_knowledge_job(
        app.state.job_db, app.state.settings, "video-src-ws", "VID001", "knowledge_vid001"
    )
    job_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
    (job_dir / "source.mp4").write_bytes(b"local video bytes")

    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/jobs/{job['id']}/video/source")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"local video bytes"


def test_get_video_job_source_falls_back_to_canonical_output(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    legacy_video_id = "knowledge_vid002"
    job = _create_video_knowledge_job(
        app.state.job_db, app.state.settings, "video-src-ws2", "VID002", legacy_video_id
    )
    job_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
    (job_dir / "source.mp4").write_bytes(b"local video bytes")

    canonical_dir = app.state.settings.videos_dir / legacy_video_id
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = canonical_dir / f"{legacy_video_id}.mp4"
    canonical_path.write_bytes(b"canonical video bytes")

    (job_dir / "source.mp4").unlink()

    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/jobs/{job['id']}/video/source")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"canonical video bytes"


def test_get_video_job_source_redirects_to_source_url(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    legacy_video_id = "knowledge_vid003"
    external_id = "VID003"
    source_url = f"https://example.com/{external_id}.mp4"
    job = _create_video_knowledge_job(
        app.state.job_db, app.state.settings, "video-src-ws3", external_id, legacy_video_id
    )
    job_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
    (job_dir / "source.mp4").write_bytes(b"local video bytes")
    (job_dir / "source.mp4").unlink()

    canonical_dir = app.state.settings.videos_dir / legacy_video_id
    if canonical_dir.exists():
        canonical_dir.rmdir()

    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/jobs/{job['id']}/video/source", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == source_url
