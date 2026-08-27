"""Materials API: auth, presign/complete flow, list, delete."""

from __future__ import annotations

import hashlib
import io
import json

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
        if payload is None:
            return None
        return ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": "materials-ws", "name": "materials-ws"},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


@pytest.fixture
def storage(client, monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _presign(client, workspace_id: str, **overrides) -> dict:
    payload = {"filename": "doc.txt", "size_bytes": 5, **overrides}
    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_materials_require_auth(anon_client) -> None:
    url = "/api/workspaces/ws-1/materials"
    assert anon_client.get(url).status_code == 401
    assert (
        anon_client.post(f"{url}/presign", json={"filename": "a", "size_bytes": 1}).status_code
        == 401
    )
    assert anon_client.post(f"{url}/m-1/complete").status_code == 401
    assert anon_client.delete(f"{url}/m-1").status_code == 401


def test_non_member_gets_404(client, storage) -> None:
    workspace_id = _create_workspace(client)
    response = client.post(
        "/api/users",
        json={"username": "materials-member", "password": "pw1"},
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post(
        "/api/auth/login", json={"username": "materials-member", "password": "pw1"}
    )
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"

    assert member.get(f"/api/workspaces/{workspace_id}/materials").status_code == 404
    response = member.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={"filename": "a.txt", "size_bytes": 1},
    )
    assert response.status_code == 404


def test_presign_without_storage_returns_503(client, monkeypatch) -> None:
    monkeypatch.setattr(client.app.state.materials_service, "storage", None)
    workspace_id = _create_workspace(client)

    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={"filename": "a.txt", "size_bytes": 1},
    )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_presign_complete_roundtrip(client, storage) -> None:
    workspace_id = _create_workspace(client)
    payload = b"roundtrip"
    content_hash = hashlib.sha256(payload).hexdigest()
    result = _presign(client, workspace_id, size_bytes=len(payload), content_hash=content_hash)

    material = result["material"]
    assert material["status"] == "uploading"
    assert result["upload_url"].endswith(f"{workspace_id}/{content_hash}/doc.txt")
    assert result["deduplicated"] is False

    # Simulate the browser PUT going straight to the object store.
    storage.objects[f"{workspace_id}/{content_hash}/doc.txt"] = payload
    response = client.post(f"/api/workspaces/{workspace_id}/materials/{material['id']}/complete")
    assert response.status_code == 200, response.text
    assert response.json()["material"]["status"] == "ready"

    # A second presign of the same content dedups onto the ready row.
    again = _presign(client, workspace_id, size_bytes=len(payload), content_hash=content_hash)
    assert again["deduplicated"] is True
    assert again["upload_url"] is None
    assert again["material"]["id"] == material["id"]


def test_complete_verification_failure_returns_422(client, storage) -> None:
    workspace_id = _create_workspace(client)
    result = _presign(client, workspace_id, size_bytes=10)
    storage.objects[f"{workspace_id}/{result['material']['id']}/doc.txt"] = b"short"

    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/{result['material']['id']}/complete"
    )

    assert response.status_code == 422
    detail = client.get(f"/api/workspaces/{workspace_id}/materials/{result['material']['id']}")
    assert detail.json()["material"]["status"] == "failed"


def test_list_and_get(client, storage) -> None:
    workspace_id = _create_workspace(client)
    for index in range(3):
        _presign(client, workspace_id, filename=f"file-{index}.txt", size_bytes=index)

    response = client.get(f"/api/workspaces/{workspace_id}/materials?limit=2&offset=0")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 3
    assert [m["filename"] for m in page["materials"]] == ["file-2.txt", "file-1.txt"]

    response = client.get(f"/api/workspaces/{workspace_id}/materials?limit=2&offset=2")
    assert [m["filename"] for m in response.json()["materials"]] == ["file-0.txt"]

    material_id = page["materials"][0]["id"]
    response = client.get(f"/api/workspaces/{workspace_id}/materials/{material_id}")
    assert response.status_code == 200
    assert response.json()["material"]["id"] == material_id
    assert client.get(f"/api/workspaces/{workspace_id}/materials/nope").status_code == 404


def test_delete_removes_material_and_object(client, storage) -> None:
    workspace_id = _create_workspace(client)
    result = _presign(client, workspace_id)
    material_id = result["material"]["id"]
    storage_key = f"{workspace_id}/{material_id}/doc.txt"
    storage.objects[storage_key] = b"12345"

    response = client.delete(f"/api/workspaces/{workspace_id}/materials/{material_id}")

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == material_id
    assert storage.deleted == [storage_key]
    assert client.get(f"/api/workspaces/{workspace_id}/materials/{material_id}").status_code == 404


def test_delete_referenced_material_returns_409(client, storage, job_db) -> None:
    """被 job input_json 引用的材料拒删（409），对象与行保留。"""
    workspace_id = _create_workspace(client)
    result = _presign(client, workspace_id)
    material_id = result["material"]["id"]
    storage_key = f"{workspace_id}/{material_id}/doc.txt"
    storage.objects[storage_key] = b"12345"
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type,"
            " source_id, input_json)"
            " values ('job-refs-material', %s, 'demo_workflow', 'material', %s, %s)",
            (
                workspace_id,
                material_id,
                json.dumps({"type": "material", "material_id": material_id}),
            ),
        )

    response = client.delete(f"/api/workspaces/{workspace_id}/materials/{material_id}")

    assert response.status_code == 409, response.text
    assert "referenced by job" in response.json()["detail"]
    assert storage.deleted == []
    assert client.get(f"/api/workspaces/{workspace_id}/materials/{material_id}").status_code == 200
