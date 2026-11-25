from __future__ import annotations

import hashlib

SOURCE_WS = "shared-api-source"
TARGET_WS = "shared-api-target"
THIRD_WS = "shared-api-third"


def create_workspaces(client) -> None:
    for workspace_id in (SOURCE_WS, TARGET_WS, THIRD_WS):
        response = client.post("/api/workspaces", json={"id": workspace_id, "name": workspace_id})
        assert response.status_code == 200, response.text


def ready_material(client, storage, workspace_id: str, payload: bytes, filename: str) -> str:
    content_hash = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": filename,
            "size_bytes": len(payload),
            "content_type": "text/plain",
            "content_hash": content_hash,
        },
    )
    assert response.status_code == 200, response.text
    material_id = response.json()["material"]["id"]
    storage.objects[f"{workspace_id}/{content_hash}/v2g0/m_{material_id}/{filename}"] = payload
    response = client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete")
    assert response.status_code == 200, response.text
    return material_id


def create_resource(client, workspace_id: str, material_id: str, token: str = "api-v1") -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/shared-resources",
        json={
            "name": "API review rules",
            "source": {"type": "material", "id": material_id},
            "idempotency_key": token,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["resource"]


def create_user(client, username: str, memberships: dict[str, str]):
    response = client.post("/api/users", json={"username": username, "password": "pw1"})
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    for workspace_id, role in memberships.items():
        response = client.put(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": user_id, "role": role},
        )
        assert response.status_code == 200, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": "pw1"})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member, user_id
