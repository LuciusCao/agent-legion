from __future__ import annotations

import pytest

from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_dict
from tests.fakes.storage import FakeObjectStorage
from tests.routes.shared_resources.helpers import (
    SOURCE_WS,
    TARGET_WS,
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


def _publish(client, workspace_id: str, nodes: dict) -> None:
    definition = workflow_definition_from_dict(
        {"key": workspace_id, "label": "Run confirmation", "nodes": nodes}
    )
    WorkflowRevisionService(client.app.state.job_db).ensure_active_revision(
        workspace_id, definition
    )


def _url(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/shared-resource-run-confirmation"


def test_members_read_empty_confirmation_and_editor_creates_resource_free_run(
    client, storage
) -> None:
    create_workspaces(client)
    _publish(
        client,
        SOURCE_WS,
        {"draft": {"type": "code", "capability": "draft"}},
    )
    editor, _ = create_user(client, "run-confirmation-editor", {SOURCE_WS: "editor"})
    viewer, _ = create_user(client, "run-confirmation-viewer", {SOURCE_WS: "viewer"})
    outsider, _ = create_user(client, "run-confirmation-outsider", {TARGET_WS: "editor"})

    for member in (client, editor, viewer):
        response = member.get(_url(SOURCE_WS))
        assert response.status_code == 200, response.text
        assert response.json() == {"nodes": [], "resources": []}
    assert outsider.get(_url(SOURCE_WS)).status_code == 404
    assert editor.get(f"/api/workspaces/{SOURCE_WS}/workflow-revisions/active").status_code == 403

    material_id = ready_material(client, storage, SOURCE_WS, b"run", "run.txt")
    response = editor.post(
        f"/api/workspaces/{SOURCE_WS}/runs",
        json={"mode": "sync", "items": [{"type": "material", "material_id": material_id}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_count"] == 1


def test_confirmation_uses_current_version_and_legacy_nodes_without_excess_metadata(
    client, storage
) -> None:
    create_workspaces(client)
    first_material = ready_material(client, storage, SOURCE_WS, b"v1", "rules-v1.txt")
    resource = create_resource(client, SOURCE_WS, first_material)
    second_material = ready_material(client, storage, SOURCE_WS, b"v2", "rules-v2.txt")
    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/versions",
        json={
            "source": {"type": "material", "id": second_material},
            "idempotency_key": "confirmation-v2",
        },
    )
    assert response.status_code == 200, response.text
    response = client.put(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/selection",
        json={"version": 2, "expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    _publish(
        client,
        SOURCE_WS,
        {
            "modern": {
                "type": "code",
                "capability": "modern",
                "shared_resources": [resource["id"]],
            },
            "legacy": {
                "type": "node",
                "capability": "legacy",
                "shared_resources": [resource["id"]],
            },
            "unresolved": {
                "type": "code",
                "capability": "unresolved",
                "shared_resources": ["not-readable"],
            },
        },
    )
    viewer, _ = create_user(client, "run-confirmation-resource-viewer", {SOURCE_WS: "viewer"})

    expected_nodes = {
        "modern": [resource["id"]],
        "legacy": [resource["id"]],
        "unresolved": ["not-readable"],
    }
    expected_resources = [
        {
            "resource_id": resource["id"],
            "name": "API review rules",
            "version": 2,
            "access_status": "active",
        }
    ]
    for member in (client, viewer):
        response = member.get(_url(SOURCE_WS))
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"nodes", "resources"}
        assert {
            node["node_key"]: node["resource_ids"] for node in payload["nodes"]
        } == expected_nodes
        assert payload["resources"] == expected_resources
        assert set(payload["resources"][0]) == {
            "resource_id",
            "name",
            "version",
            "access_status",
        }


def test_confirmation_keeps_revoked_selection_but_omits_unknown_resource(client, storage) -> None:
    create_workspaces(client)
    material_id = ready_material(client, storage, SOURCE_WS, b"shared", "shared.txt")
    resource = create_resource(client, SOURCE_WS, material_id)
    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/shares",
        json={"target_workspace_id": TARGET_WS},
    )
    assert response.status_code == 200, response.text
    _publish(
        client,
        TARGET_WS,
        {
            "draft": {
                "type": "code",
                "capability": "draft",
                "shared_resources": [resource["id"], "unknown-resource"],
            }
        },
    )
    response = client.delete(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource['id']}/shares/{TARGET_WS}"
    )
    assert response.status_code == 200, response.text
    viewer, _ = create_user(client, "run-confirmation-revoked-viewer", {TARGET_WS: "viewer"})

    response = viewer.get(_url(TARGET_WS))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "nodes": [
            {
                "node_key": "draft",
                "resource_ids": [resource["id"], "unknown-resource"],
            }
        ],
        "resources": [
            {
                "resource_id": resource["id"],
                "name": "API review rules",
                "version": 1,
                "access_status": "revoked",
            }
        ],
    }


def test_confirmation_without_active_revision_is_not_found(client) -> None:
    create_workspaces(client)
    response = client.get(_url(SOURCE_WS))
    assert response.status_code == 404
    assert response.json()["detail"] == "No active workflow revision"
