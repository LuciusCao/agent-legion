"""Material bundles API (#156): auth, manifest CRUD, delete guard."""

from __future__ import annotations

import hashlib
import io

import pytest

from server.app.storage import ObjectHead

CSRF = {"x-agent-legion-request": "1"}


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


@pytest.fixture
def storage(client, monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "bundles-ws"},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


def _ready_material(
    client, storage: FakeStorage, workspace_id: str, payload: bytes, name: str
) -> str:
    content_hash = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": name,
            "size_bytes": len(payload),
            "content_type": "text/plain",
            "content_hash": content_hash,
        },
    )
    assert response.status_code == 200, response.text
    material_id = response.json()["material"]["id"]
    storage.objects[f"{workspace_id}/{content_hash}/{name}"] = payload
    response = client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete")
    assert response.status_code == 200, response.text
    return material_id


def _create_bundle(client, storage: FakeStorage, workspace_id: str) -> dict:
    first = _ready_material(client, storage, workspace_id, b"alpha", "a.txt")
    second = _ready_material(client, storage, workspace_id, b"beta", "b.txt")
    response = client.post(
        f"/api/workspaces/{workspace_id}/material-bundles",
        json={
            "name": "folder",
            "members": [
                {"material_id": first, "path": "a.txt"},
                {"material_id": second, "path": "sub/b.txt"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["bundle"]


def test_bundles_require_auth(anon_client) -> None:
    url = "/api/workspaces/ws-1/material-bundles"
    assert anon_client.get(url).status_code == 401
    assert anon_client.get(f"{url}/b-1").status_code == 401
    assert (
        anon_client.post(
            url, json={"name": "b", "members": [{"material_id": "m", "path": "p"}]}
        ).status_code
        == 401
    )
    assert anon_client.delete(f"{url}/b-1").status_code == 401


def test_create_get_list_delete_roundtrip(client, storage) -> None:
    workspace_id = _create_workspace(client)
    bundle = _create_bundle(client, storage, workspace_id)

    assert bundle["name"] == "folder"
    assert bundle["file_count"] == 2
    assert [member["path"] for member in bundle["members"]] == ["a.txt", "sub/b.txt"]

    response = client.get(f"/api/workspaces/{workspace_id}/material-bundles/{bundle['id']}")
    assert response.status_code == 200
    assert response.json()["bundle"]["id"] == bundle["id"]

    listed = client.get(f"/api/workspaces/{workspace_id}/material-bundles").json()
    assert listed["total"] == 1
    assert listed["bundles"][0]["id"] == bundle["id"]

    response = client.delete(f"/api/workspaces/{workspace_id}/material-bundles/{bundle['id']}")
    assert response.status_code == 200
    assert (
        client.get(f"/api/workspaces/{workspace_id}/material-bundles/{bundle['id']}").status_code
        == 404
    )


def test_create_rejects_invalid_member_path(client, storage) -> None:
    workspace_id = _create_workspace(client)
    material_id = _ready_material(client, storage, workspace_id, b"x", "x.txt")

    response = client.post(
        f"/api/workspaces/{workspace_id}/material-bundles",
        json={"name": "b", "members": [{"material_id": material_id, "path": "../evil.txt"}]},
    )

    assert response.status_code == 400
    assert "segments" in response.json()["detail"]


def test_create_unknown_material_returns_404(client, storage) -> None:
    workspace_id = _create_workspace(client)

    response = client.post(
        f"/api/workspaces/{workspace_id}/material-bundles",
        json={"name": "b", "members": [{"material_id": "mat-missing", "path": "a.txt"}]},
    )

    assert response.status_code == 404


def test_delete_referenced_bundle_returns_409(client, storage, job_db) -> None:
    import json

    workspace_id = _create_workspace(client)
    bundle = _create_bundle(client, storage, workspace_id)
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type,"
            " source_id, status, input_json)"
            " values ('job-bundle-ref', %s, 'demo_workflow', 'bundle', %s, 'queued', %s)",
            (
                workspace_id,
                bundle["id"],
                json.dumps({"type": "bundle", "bundle_id": bundle["id"]}),
            ),
        )

    response = client.delete(f"/api/workspaces/{workspace_id}/material-bundles/{bundle['id']}")

    assert response.status_code == 409
