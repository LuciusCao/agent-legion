from __future__ import annotations

import pytest

from server.app.jobs.queries.shared_resources.lifecycle import insert_job_pin_tx
from server.app.services.shared_resources.service import SharedResourcesService
from tests.fakes.storage import FakeObjectStorage
from tests.routes.shared_resources.helpers import (
    SOURCE_WS,
    TARGET_WS,
    THIRD_WS,
    create_resource,
    create_workspaces,
    ready_material,
)


@pytest.fixture
def storage(client, monkeypatch) -> FakeObjectStorage:
    fake = FakeObjectStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def test_deleted_owner_cannot_be_inherited_by_recreated_workspace(client, storage, job_db) -> None:
    create_workspaces(client)
    original_material = ready_material(client, storage, SOURCE_WS, b"owner-v1", "owner.txt")
    resource = create_resource(client, SOURCE_WS, original_material)
    resource_id = resource["id"]
    newer_material = ready_material(client, storage, SOURCE_WS, b"owner-v2", "owner-v2.txt")
    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/versions",
        json={"source": {"type": "material", "id": newer_material}, "idempotency_key": "owner-v2"},
    )
    assert response.status_code == 200, response.text

    response = client.post(
        f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares",
        json={"target_workspace_id": TARGET_WS},
    )
    assert response.status_code == 200, response.text
    target_grant_id = response.json()["grant"]["id"]
    v1 = client.get(
        f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}/versions/1"
    ).json()["version"]
    with job_db.write() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('target-owner-tombstone-job', %s, 'test', 'pin', 'queued', '{}')",
            (TARGET_WS,),
        )
        insert_job_pin_tx(
            conn,
            job_id="target-owner-tombstone-job",
            resource_id=resource_id,
            version_id=v1["id"],
            manifest_hash=v1["manifest_hash"],
            grant_id=target_grant_id,
        )

    response = client.delete(f"/api/workspaces/{SOURCE_WS}")
    assert response.status_code == 200, response.text
    with job_db.read() as conn:
        owner_row = conn.execute(
            "select owner_deleted_at from shared_resources where id=%s", (resource_id,)
        ).fetchone()
        target_grant = conn.execute(
            "select status from shared_resource_grants where id=%s", (target_grant_id,)
        ).fetchone()
        pin_count = conn.execute(
            "select count(*) as count from shared_resource_job_pins where resource_id=%s",
            (resource_id,),
        ).fetchone()["count"]
    assert owner_row["owner_deleted_at"] is not None
    assert target_grant["status"] == "active"
    assert pin_count == 1

    response = client.post("/api/workspaces", json={"id": SOURCE_WS, "name": "Replacement"})
    assert response.status_code == 200, response.text
    replacement_material = ready_material(
        client, storage, SOURCE_WS, b"replacement-source", "replacement.txt"
    )
    owner_urls = {
        "self_share": (
            "POST",
            f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares",
            {"target_workspace_id": SOURCE_WS},
        ),
        "other_share": (
            "POST",
            f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares",
            {"target_workspace_id": THIRD_WS},
        ),
        "revoke": (
            "DELETE",
            f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares/{TARGET_WS}",
            None,
        ),
        "delete_version": (
            "DELETE",
            f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/versions/2",
            None,
        ),
        "create_version": (
            "POST",
            f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/versions",
            {
                "source": {"type": "material", "id": replacement_material},
                "idempotency_key": "replacement-version",
            },
        ),
        "create_resource_idempotency": (
            "POST",
            f"/api/workspaces/{SOURCE_WS}/shared-resources",
            {
                "name": "API review rules",
                "source": {"type": "material", "id": replacement_material},
                "idempotency_key": "api-v1",
            },
        ),
    }
    for operation, (method, url, payload) in owner_urls.items():
        response = client.request(method, url, json=payload)
        assert response.status_code == 404, f"{operation}: {response.status_code} {response.text}"
    assert (
        client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}").status_code == 404
    )
    assert (
        client.get(f"/api/workspaces/{SOURCE_WS}/shared-resources/{resource_id}/shares").status_code
        == 404
    )

    target_resource = client.get(f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}")
    assert target_resource.status_code == 200, target_resource.text
    assert target_resource.json()["resource"]["access_status"] == "active"
    assert target_resource.json()["resource"]["current_selection"]["version"] == 2
    target_versions = client.get(
        f"/api/workspaces/{TARGET_WS}/shared-resources/{resource_id}/versions"
    )
    assert target_versions.status_code == 200
    assert [row["version"] for row in target_versions.json()["versions"]] == [2, 1]

    service = SharedResourcesService(job_db, storage)
    for new_run in (False, True):
        resolved = service.resolve_exact_version(
            TARGET_WS,
            resource_id,
            version=1,
            manifest_hash=v1["manifest_hash"],
            for_new_run=new_run,
        )
        assert resolved["grant_id"] == target_grant_id
        assert resolved["manifest_hash"] == v1["manifest_hash"]
    with job_db.read() as conn:
        assert (
            conn.execute(
                "select count(*) as count from shared_resource_grants"
                " where resource_id=%s and workspace_id=%s",
                (resource_id, SOURCE_WS),
            ).fetchone()["count"]
            == 0
        )
        assert (
            conn.execute(
                "select count(*) as count from shared_resource_job_pins where resource_id=%s",
                (resource_id,),
            ).fetchone()["count"]
            == 1
        )

    new_resource = create_resource(
        client, SOURCE_WS, replacement_material, token="new-owner-resource"
    )
    assert new_resource["id"] != resource_id
