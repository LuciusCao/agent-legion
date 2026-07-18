import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.db import Database
from server.app.routes.packages import create_packages_router
from server.app.services.job_packages import JobPackageService, WorkspacePackageLockedError
from server.app.services.package_deletion import PackageDeletionService
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


def test_list_packages_does_not_write_stats_back(client, db, settings, monkeypatch):
    # Regression: GET /packages must be read-only. Legacy rows missing stats
    # are healed at app startup (see test_package_stats_backfill.py), not here.
    zip_path = settings.packages_dir / "legacy-no-stats.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"videos": [{"id": "v1"}, {"id": "v2"}]}))
    db.insert_package("packages/legacy-no-stats.zip")

    def forbidden_write(*args, **kwargs):
        raise AssertionError("GET /packages must not write package stats")

    monkeypatch.setattr(Database, "update_package_stats", forbidden_write)

    response = client.get("/api/packages")
    assert response.status_code == 200
    packages = response.json()["packages"]
    assert len(packages) == 1
    assert packages[0]["name"] == ""
    assert packages[0]["video_count"] == 0

    row = db.list_packages(limit=1)[0]
    assert (row["name"], row["video_count"], row["size_bytes"]) == ("", 0, 0)


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


def test_create_packages_router_builds_default_job_package_service(db, job_db, settings):
    router = create_packages_router(
        db,
        job_db,
        settings,
        PackageDeletionService(db, settings.packages_dir),
    )
    assert any(getattr(route, "path", "") == "/packages" for route in router.routes)


def test_list_packages_skips_package_root_itself(client, settings):
    # A stored path that resolves to the packages dir itself must be skipped.
    client.app.state.db.insert_package("packages", name="Root Entry")
    client.app.state.db.insert_package(
        "packages/valid.zip", name="Valid Package", video_count=1, size_bytes=100
    )

    response = client.get("/api/packages")
    assert response.status_code == 200
    data = response.json()
    assert [pkg["name"] for pkg in data["packages"]] == ["Valid Package"]


def test_delete_package_success(client, db, settings):
    zip_path = settings.packages_dir / "deletable.zip"
    zip_path.write_bytes(b"fake-zip")
    db.insert_package("packages/deletable.zip", name="Deletable")
    pkg = db.list_packages(limit=1)[0]

    response = client.delete(f"/api/packages/{pkg['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not zip_path.exists()
    assert db.list_packages(limit=10) == []


def test_patch_package_not_found(client):
    response = client.patch("/api/packages/99999", json={"name": "New"})
    assert response.status_code == 404


def test_patch_package_updates_name(client, db, tmp_path):
    db.insert_package(str(tmp_path / "rename-me.zip"), name="Old Name", locked=0)
    pkg = db.list_packages(limit=1)[0]

    response = client.patch(f"/api/packages/{pkg['id']}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

    updated = db.list_packages(limit=1)[0]
    assert updated["name"] == "New Name"


def test_list_workspace_packages_skips_bad_paths(workspace_client):
    ws = workspace_client.post(
        "/api/workspaces",
        json={"name": "Bad Paths WS", "default_workflow_key": "question_comprehension_info"},
    )
    assert ws.status_code == 200
    ws_id = ws.json()["workspace"]["id"]

    job_db = workspace_client.app.state.job_db
    job_db.insert_workspace_package(ws_id, "../escaped.zip", name="Escaped")
    job_db.insert_workspace_package(ws_id, "packages/not-in-workspace.zip", name="Outside")
    job_db.insert_workspace_package(
        ws_id, f"packages/workspace-{ws_id}/ok.zip", name="OK", job_count=2, size_bytes=10
    )

    response = workspace_client.get(f"/api/workspaces/{ws_id}/packages")
    assert response.status_code == 200
    packages = response.json()["packages"]
    assert len(packages) == 1
    assert packages[0]["name"] == "OK"
    assert packages[0]["video_count"] == 2
    returned_path = Path(packages[0]["path"])
    assert returned_path.is_absolute()
    assert returned_path.as_posix().endswith(f"packages/workspace-{ws_id}/ok.zip")


def test_delete_workspace_package_not_found(client):
    response = client.delete("/api/workspaces/ws-x/packages/99999")
    assert response.status_code == 404


def test_update_workspace_package_not_found(client):
    response = client.patch("/api/workspaces/ws-x/packages/99999", json={"name": "New"})
    assert response.status_code == 404


def test_update_workspace_package_locked_returns_400(client, monkeypatch):
    def raise_locked(self, workspace_id, package_id, name):
        raise WorkspacePackageLockedError("Package is locked")

    monkeypatch.setattr(JobPackageService, "rename_workspace_package", raise_locked)

    response = client.patch("/api/workspaces/ws-x/packages/1", json={"name": "New"})
    assert response.status_code == 400
    assert "locked" in response.json()["detail"].lower()
