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
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
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
    (storage_dir / "questions.json").write_text('{"question_id":"' + question_id + '"}')

    # There is no public "force complete" endpoint, so mutate the job status
    # directly through the internal DB handle.
    job_db.update_job_status(job_id, "completed")
    return job_id


def test_list_workspace_packages_empty_for_new_workspace(workspace_client):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Empty Packages WS", "default_workflow_key": "question_comprehension_info"},
    )
    assert ws.status_code == 200
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages")
    assert response.status_code == 200
    assert response.json() == {"packages": []}


def test_create_workspace_package_job_accepted(workspace_client):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Package Job WS", "default_workflow_key": "question_comprehension_info"},
    )
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
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "No Jobs WS", "default_workflow_key": "question_comprehension_info"},
    )
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.post(
        f"/api/workspaces/{ws_id}/jobs/package",
        json={"job_ids": []},
    )
    assert response.status_code == 400
    assert "job_ids" in response.json()["detail"].lower()


def test_create_workspace_package_job_rejects_incomplete_jobs(workspace_client):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Incomplete WS", "default_workflow_key": "question_comprehension_info"},
    )
    ws_id = ws.json()["workspace"]["id"]

    created = workspace_client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
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
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Traverse WS", "default_workflow_key": "question_comprehension_info"},
    )
    ws_id = ws.json()["workspace"]["id"]

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/%2e%2e/%2e%2e/etc/passwd")
    assert response.status_code == 404

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/foo/bar/../baz")
    assert response.status_code == 404

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/%2fetc%2fpasswd")
    assert response.status_code == 404


def test_workspace_package_download_rejects_subdirectory(workspace_client, tmp_path):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Subdir WS", "default_workflow_key": "question_comprehension_info"},
    )
    ws_id = ws.json()["workspace"]["id"]

    packages_dir = workspace_client.app.state.settings.packages_dir / f"workspace-{ws_id}"
    nested = packages_dir / "sub" / "bad.zip"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("fake", encoding="utf-8")

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages/sub/bad.zip")
    assert response.status_code == 404


def test_workspace_package_lifecycle_rename_lock_delete(workspace_client):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Lifecycle WS", "default_workflow_key": "question_comprehension_info"},
    )
    ws_id = ws.json()["workspace"]["id"]
    job_id = _create_completed_job(workspace_client, ws_id, "Q501")

    create_resp = workspace_client.post(
        f"/api/workspaces/{ws_id}/jobs/package", json={"job_ids": [job_id]}
    )
    assert create_resp.status_code == 200

    list_resp = workspace_client.get(f"/api/workspaces/{ws_id}/packages")
    assert list_resp.status_code == 200
    packages = list_resp.json()["packages"]
    assert len(packages) == 1
    pkg_id = packages[0]["id"]
    assert packages[0]["video_count"] == 1
    assert packages[0]["size_bytes"] > 0

    patch_resp = workspace_client.patch(
        f"/api/workspaces/{ws_id}/packages/{pkg_id}", json={"name": "Renamed"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Renamed"

    lock_resp = workspace_client.patch(
        f"/api/workspaces/{ws_id}/packages/{pkg_id}", json={"locked": True}
    )
    assert lock_resp.status_code == 200
    assert lock_resp.json()["locked"] is True

    del_resp = workspace_client.delete(f"/api/workspaces/{ws_id}/packages/{pkg_id}")
    assert del_resp.status_code == 400
    assert "locked" in del_resp.json()["detail"].lower()

    workspace_client.patch(f"/api/workspaces/{ws_id}/packages/{pkg_id}", json={"locked": False})
    del_resp = workspace_client.delete(f"/api/workspaces/{ws_id}/packages/{pkg_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    list_resp = workspace_client.get(f"/api/workspaces/{ws_id}/packages")
    assert list_resp.json()["packages"] == []


def test_workspace_package_download_missing_file_returns_404(client):
    response = client.get("/api/workspaces/ws-x/packages/nope.zip")
    assert response.status_code == 404


def test_workspace_package_download_rejects_symlink_escape(client, settings, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    packages_dir = settings.packages_dir / "workspace-ws-x"
    packages_dir.mkdir(parents=True)
    (packages_dir / "evil.zip").symlink_to(outside)

    response = client.get("/api/workspaces/ws-x/packages/evil.zip")
    assert response.status_code == 404
