from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from tests.fakes.storage import FakeObjectStorage
from tests.routes.shared_resources.helpers import (
    SOURCE_WS,
    TARGET_WS,
    create_resource,
    create_workspaces,
    ready_material,
)


@pytest.fixture
def storage(client, monkeypatch) -> FakeObjectStorage:
    fake = FakeObjectStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def test_shared_resource_endpoints_require_auth(anon_client) -> None:
    url = f"/api/workspaces/{SOURCE_WS}/shared-resources"
    assert anon_client.get(url).status_code == 401
    assert anon_client.get(f"{url}/resource-1").status_code == 401
    assert (
        anon_client.post(
            url,
            json={
                "name": "x",
                "source": {"type": "material", "id": "m-1"},
                "idempotency_key": "token",
            },
        ).status_code
        == 401
    )
    assert anon_client.put(f"{url}/resource-1/selection", json={}).status_code == 401


def test_resource_share_version_and_revoke_api_roundtrip(client, storage) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"api-v1", "rules.txt")
    resource = create_resource(client, SOURCE_WS, material_id)
    resource_id = resource["id"]

    assert client.get(f"/api/workspaces/{TARGET_WS}/shared-resources").json()["total"] == 0
    assert (
        client.get(f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}").status_code == 404
    )

    object_count = len(storage.objects)
    share_url = f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares"
    response = client.post(share_url, json={"target_workspace_id": TARGET_WS})
    assert response.status_code == 200, response.text
    assert response.json()["duplicate"] is False
    assert len(storage.objects) == object_count
    grant_id = response.json()["grant"]["id"]
    response = client.post(share_url, json={"target_workspace_id": TARGET_WS})
    assert response.json()["duplicate"] is True
    assert response.json()["grant"]["id"] == grant_id

    target_resource = client.get(f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}")
    assert target_resource.status_code == 200
    assert target_resource.json()["resource"]["current_selection"]["version"] == 1

    newer_material = ready_material(client, storage, SOURCE_WS, b"api-v2", "rules-v2.txt")
    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/versions",
        json={"source": {"type": "material", "id": newer_material}, "idempotency_key": "api-v2"},
    )
    assert response.status_code == 200, response.text
    v2 = response.json()["version"]
    assert v2["version"] == 2

    selection_url = f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}/selection"
    response = client.put(selection_url, json={"version": 2, "expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["selection"]["revision"] == 2
    response = client.put(selection_url, json={"version": 1, "expected_revision": 1})
    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == 2
    response = client.put(selection_url, json={"version": 1, "expected_revision": 2})
    assert response.status_code == 200, response.text
    assert response.json()["selection"]["revision"] == 3

    response = client.get(f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}/versions/1")
    assert response.status_code == 200
    assert "storage_key" not in json.dumps(response.json())
    assert "source_etag" not in json.dumps(response.json())

    response = client.delete(f"{share_url}/{TARGET_WS}")
    assert response.status_code == 200, response.text
    assert response.json()["grant"]["status"] == "revoked"
    response = client.put(selection_url, json={"version": 2, "expected_revision": 3})
    assert response.status_code == 409

    response = client.delete(f"/api/workspaces/{SOURCE_WS}/materials/{material_id}")
    assert response.status_code == 200, response.text
    response = client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/versions/1")
    assert response.status_code == 200
    assert response.json()["version"]["files"][0]["sha256"]


def test_copy_failure_is_not_visible_and_retries_the_same_resource(
    client, storage, monkeypatch
) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"api-failure", "failure.txt")
    real_copy = storage.copy_object

    def fail_copy(source_key, destination_key, source_etag=""):
        raise ClientError({"Error": {"Code": "InternalError"}}, "CopyObject")

    monkeypatch.setattr(storage, "copy_object", fail_copy)
    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources",
        json={
            "name": "API review rules",
            "source": {"type": "material", "id": material_id},
            "idempotency_key": "retry-api",
        },
    )
    assert response.status_code == 422, response.text
    assert client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources").json()["total"] == 0

    monkeypatch.setattr(storage, "copy_object", real_copy)
    resource = create_resource(client, SOURCE_WS, material_id, token="retry-api")
    assert resource["name"] == "API review rules"
    assert client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources").json()["total"] == 1


def test_create_uses_the_dynamic_material_storage_seam(client, storage, monkeypatch) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"storage-off", "storage.txt")
    monkeypatch.setattr(client.app.state.materials_service, "storage", None)

    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources",
        json={
            "name": "No storage",
            "source": {"type": "material", "id": material_id},
            "idempotency_key": "no-storage",
        },
    )

    assert response.status_code == 503
    assert client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources").json()["total"] == 0
