from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.storage_paths import resolve_job_dir


@pytest.fixture
def workspace_client(client):
    """Ensure workflows are enabled and provide a helper to create workspaces/jobs."""
    client.app.state.settings.config.setdefault("workflows", {})["enabled"] = True
    return client


def _create_completed_job(client: TestClient, workspace_id: str, question_id: str = "Q001"):
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "question_content",
            "source_kind": "direct_ids",
            "question_ids": [question_id],
            "knowledge_codes": [],
        },
    )
    assert created.status_code == 200
    job = created.json()["jobs"][0]
    job_id = job["id"]

    # Write a minimal artifact so workspace packaging has something to archive.
    job_db = client.app.state.job_db
    record = job_db.get_job(job_id)
    storage_dir = resolve_job_dir(record, client.app.state.settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "question_context.json").write_text('{"question_id":"' + question_id + '"}')

    # There is no public "force complete" endpoint, so mutate the job status
    # directly through the internal DB handle.
    job_db.update_job_status(job_id, "completed")
    return job_id


def _sync_submit(fn):
    fn()


def _create_completed_video(client: TestClient, external_id: str) -> str:
    response = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": f"https://example.com/{external_id}.mp4",
                    "content_type": "knowledge",
                    "external_id": external_id,
                }
            ]
        },
    )
    assert response.status_code == 200
    video_id = f"knowledge_{external_id}"
    settings = client.app.state.settings
    video_dir = settings.videos_dir / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "metadata.json").write_text(f'{{"id":"{video_id}"}}', encoding="utf-8")
    client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")
    return video_id


def test_list_workspace_packages_empty_for_new_workspace(workspace_client):
    ws = workspace_client.post("/api/workspaces", json={"name": "Empty Packages WS"})
    assert ws.status_code == 200
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages")
    assert response.status_code == 200
    assert response.json() == {"packages": []}


def test_create_workspace_package_job_accepted(workspace_client):
    ws = workspace_client.post("/api/workspaces", json={"name": "Package Job WS"})
    ws_id = ws.json()["workspace"]["id"]
    job_id = _create_completed_job(workspace_client, ws_id, "Q101")

    response = workspace_client.post(
        f"/api/workspaces/{ws_id}/jobs/package",
        json={"job_ids": [job_id]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 0
    assert body["results"][0]["status"] == "succeeded"
    assert body["package_filename"]
    assert body["download_url"]

    settings = workspace_client.app.state.settings
    workspace_packages_dir = settings.packages_dir / f"workspace-{ws_id}"
    zip_files = [p for p in workspace_packages_dir.iterdir() if p.suffix == ".zip"]
    assert len(zip_files) == 1
    assert zip_files[0].stat().st_size > 0


def test_create_workspace_package_job_rejects_no_job_ids(workspace_client):
    ws = workspace_client.post("/api/workspaces", json={"name": "No Jobs WS"})
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.post(
        f"/api/workspaces/{ws_id}/jobs/package",
        json={"job_ids": []},
    )
    assert response.status_code == 400
    assert "job_ids" in response.json()["detail"].lower()


def test_create_workspace_package_job_rejects_incomplete_jobs(workspace_client):
    ws = workspace_client.post("/api/workspaces", json={"name": "Incomplete WS"})
    ws_id = ws.json()["workspace"]["id"]

    created = workspace_client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "question_content",
            "source_kind": "direct_ids",
            "question_ids": ["Q201"],
            "knowledge_codes": [],
        },
    )
    job_id = created.json()["jobs"][0]["id"]
    # Leave job status as queued (not completed).

    response = workspace_client.post(
        f"/api/workspaces/{ws_id}/jobs/package",
        json={"job_ids": [job_id]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded_count"] == 0
    assert body["failed_count"] == 1
    assert body["results"][0]["reason_code"] == "not_completed"


def test_workspace_package_download_rejects_path_traversal(workspace_client):
    ws = workspace_client.post("/api/workspaces", json={"name": "Traverse WS"})
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/%2e%2e/%2e%2e/etc/passwd")
    assert response.status_code == 404

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/foo/bar/../baz")
    assert response.status_code == 404

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/%2fetc%2fpasswd")
    assert response.status_code == 404


def test_workspace_package_download_rejects_subdirectory(workspace_client, tmp_path):
    ws = workspace_client.post("/api/workspaces", json={"name": "Subdir WS"})
    ws_id = ws.json()["workspace"]["id"]

    packages_dir = workspace_client.app.state.settings.packages_dir / f"workspace-{ws_id}"
    nested = packages_dir / "sub" / "bad.zip"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("fake", encoding="utf-8")

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/sub/bad.zip")
    assert response.status_code == 404


def test_delete_package_rejects_locked_package(client, db, tmp_path):
    db.insert_package(str(tmp_path / "locked-package.zip"), name="Locked Package", locked=1)
    pkg = db.list_packages(limit=1)[0]

    response = client.delete(f"/api/packages/{pkg['id']}")
    assert response.status_code == 400
    assert "locked" in response.json()["detail"].lower()


def test_patch_package_updates_locked(client, db, tmp_path):
    db.insert_package(str(tmp_path / "patchable-package.zip"), name="Patchable", locked=0)
    pkg = db.list_packages(limit=1)[0]

    response = client.patch(f"/api/packages/{pkg['id']}", json={"locked": True})
    assert response.status_code == 200
    assert response.json()["locked"] is True

    updated = db.list_packages(limit=1)[0]
    assert updated["locked"] == 1


def test_patch_package_unlocks(client, db, tmp_path):
    db.insert_package(str(tmp_path / "locked-package.zip"), name="Locked", locked=1)
    pkg = db.list_packages(limit=1)[0]

    response = client.patch(f"/api/packages/{pkg['id']}", json={"locked": False})
    assert response.status_code == 200
    assert response.json()["locked"] is False

    updated = db.list_packages(limit=1)[0]
    assert updated["locked"] == 0


def test_patch_package_unknown_field_ignored(client, db, tmp_path):
    db.insert_package(str(tmp_path / "another-package.zip"), name="Original", locked=0)
    pkg = db.list_packages(limit=1)[0]

    response = client.patch(
        f"/api/packages/{pkg['id']}",
        json={"locked": True, "unknown": "x"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result == {"id": pkg["id"], "locked": True}
    assert "unknown" not in result


def test_create_package_stores_relative_path(client, monkeypatch):
    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)
    _create_completed_video(client, "K001")

    response = client.post("/api/package", json={"video_ids": ["knowledge_K001"]})
    assert response.status_code == 200
    assert response.json()["accepted"] is True

    record = client.app.state.db.list_packages(limit=1)[0]
    assert not Path(record["path"]).is_absolute()
    assert record["path"].startswith("packages/")
    assert record["path"].endswith(".zip")


def test_list_packages_resolves_absolute_path(client, monkeypatch):
    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)
    _create_completed_video(client, "K002")

    client.post("/api/package", json={"video_ids": ["knowledge_K002"]})

    response = client.get("/api/packages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["packages"]) == 1

    record_path = Path(data["packages"][0]["path"])
    assert record_path.is_absolute()
    assert record_path.is_relative_to(client.app.state.settings.packages_dir)
    assert record_path.exists()
    assert data["packages"][0]["video_count"] == 1


def test_list_packages_skips_escaping_path(client, settings):
    # Insert a valid relative package record and an escaping one.
    client.app.state.db.insert_package(
        "packages/valid.zip", name="Valid Package", video_count=1, size_bytes=100
    )
    client.app.state.db.insert_package(
        "../escaped.zip", name="Escaped Package", video_count=1, size_bytes=100
    )

    response = client.get("/api/packages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["name"] == "Valid Package"
    returned_path = Path(data["packages"][0]["path"])
    assert returned_path.is_absolute()
    assert returned_path.is_relative_to(settings.packages_dir)


def test_delete_package_removes_relative_package(client, monkeypatch):
    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)
    _create_completed_video(client, "K003")

    client.post("/api/package", json={"video_ids": ["knowledge_K003"]})
    record = client.app.state.db.list_packages(limit=1)[0]
    package_path = client.app.state.settings.data_dir / record["path"]
    assert package_path.exists()

    response = client.delete(f"/api/packages/{record['id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not package_path.exists()
    assert client.app.state.db.list_packages(limit=10) == []
