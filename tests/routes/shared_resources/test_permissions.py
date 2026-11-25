from __future__ import annotations

import pytest

from tests.fakes.storage import FakeObjectStorage
from tests.routes.shared_resources.helpers import (
    SOURCE_WS,
    TARGET_WS,
    THIRD_WS,
    create_resource,
    create_user,
    create_workspaces,
    ready_material,
)


@pytest.fixture
def storage(client, monkeypatch) -> FakeObjectStorage:
    fake = FakeObjectStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def test_share_requires_source_read_target_write_and_full_session(client, storage) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"permissions", "permissions.txt")
    resource = create_resource(client, SOURCE_WS, material_id)
    share_url = f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/shares"
    member, _ = create_user(
        client,
        "share-source-viewer-target-editor",
        {SOURCE_WS: "viewer", TARGET_WS: "editor"},
    )

    object_count = len(storage.objects)
    response = member.post(share_url, json={"target_workspace_id": TARGET_WS})
    assert response.status_code == 200, response.text
    assert len(storage.objects) == object_count

    response = member.post(share_url, json={"target_workspace_id": THIRD_WS})
    assert response.status_code == 404
    target_viewer, _ = create_user(
        client,
        "share-target-viewer",
        {SOURCE_WS: "viewer", THIRD_WS: "viewer"},
    )
    response = target_viewer.post(share_url, json={"target_workspace_id": THIRD_WS})
    assert response.status_code == 403
    assert target_viewer.get(f"/api/workspaces/{THIRD_WS}/shared-resources").json()["total"] == 0

    outsider, _ = create_user(client, "share-source-outsider", {TARGET_WS: "editor"})
    response = outsider.post(share_url, json={"target_workspace_id": TARGET_WS})
    assert response.status_code == 404

    from server.app.auth import scoped_tokens

    admin_id = client.app.state.job_db.get_user_credentials("admin")["id"]
    scoped = client.__class__(client.app)
    token = scoped_tokens.mint_scoped_token(client.app.state.job_db, admin_id)
    scoped.headers["authorization"] = f"Bearer {token}"
    assert scoped.post(share_url, json={"target_workspace_id": TARGET_WS}).status_code == 403


def test_viewer_cannot_create_select_revoke_or_delete(client, storage) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"readonly", "readonly.txt")
    resource = create_resource(client, SOURCE_WS, material_id)
    client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/shares",
        json={"target_workspace_id": TARGET_WS},
    )
    viewer, _ = create_user(client, "resource-viewer", {SOURCE_WS: "viewer", TARGET_WS: "viewer"})

    assert (
        viewer.get(f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}").status_code
        == 200
    )
    response = viewer.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources",
        json={
            "name": "viewer cannot create",
            "source": {"type": "material", "id": material_id},
            "idempotency_key": "viewer-create",
        },
    )
    assert response.status_code == 403
    response = viewer.put(
        f"/api/workspaces/{TARGET_WS}/shared-resources/{resource['id']}/selection",
        json={"version": 1, "expected_revision": 1},
    )
    assert response.status_code == 403
    response = viewer.delete(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/shares/{TARGET_WS}"
    )
    assert response.status_code == 403
    response = viewer.delete(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/versions/1"
    )
    assert response.status_code == 403
    assert (
        client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}").json()[
            "resource"
        ]["current_selection"]["revision"]
        == 1
    )
