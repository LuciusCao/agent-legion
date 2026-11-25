from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.shared_resources.errors import (
    SharedResourceInUseError,
    SharedResourceSelectionConflictError,
)
from tests.services.shared_resources.conftest import SOURCE_WS, TARGET_WS, THIRD_WS
from tests.services.shared_resources.helpers import create_resource, ready_material


def _resource(service, materials, storage, payload: bytes = b"lifecycle-v1") -> dict:
    material_id = ready_material(materials, storage, SOURCE_WS, payload, "rules.txt")
    return create_resource(service, SOURCE_WS, "material", material_id)


def _new_version(service, materials, storage, resource_id: str, payload: bytes = b"lifecycle-v2"):
    material_id = ready_material(materials, storage, SOURCE_WS, payload, "rules-v2.txt")
    return service.create_version(
        SOURCE_WS,
        resource_id,
        source_type="material",
        source_id=material_id,
        idempotency_key=f"version-{payload.decode()}",
        created_by="editor-1",
    )


def test_share_grants_metadata_without_copying_and_pins_each_workspace_independently(
    shared_service, shared_materials, shared_storage
) -> None:
    resource = _resource(shared_service, shared_materials, shared_storage)
    with pytest.raises(NotFoundError):
        shared_service.get(TARGET_WS, resource["id"])

    object_count = len(shared_storage.objects)
    shared = shared_service.share(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="source-viewer"
    )
    assert shared["duplicate"] is False
    assert len(shared_storage.objects) == object_count
    assert shared_service.get(TARGET_WS, resource["id"])["name"] == resource["name"]
    assert shared_service.get(TARGET_WS, resource["id"])["current_selection"]["version"] == 1

    duplicate = shared_service.share(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="source-viewer"
    )
    assert duplicate["duplicate"] is True
    assert duplicate["grant"]["id"] == shared["grant"]["id"]
    assert len(shared_storage.objects) == object_count

    v2 = _new_version(shared_service, shared_materials, shared_storage, resource["id"])
    assert shared_service.get(TARGET_WS, resource["id"])["current_selection"]["version"] == 1
    selected = shared_service.select_version(
        TARGET_WS,
        resource["id"],
        version=2,
        expected_revision=1,
        actor_id="target-editor",
    )
    assert selected["revision"] == 2
    assert selected["manifest_hash"] == v2["manifest_hash"]
    unchanged = shared_service.select_version(
        TARGET_WS,
        resource["id"],
        version=2,
        expected_revision=2,
        actor_id="target-editor",
    )
    assert unchanged["revision"] == 2
    with pytest.raises(SharedResourceSelectionConflictError) as stale:
        shared_service.select_version(
            TARGET_WS,
            resource["id"],
            version=1,
            expected_revision=1,
            actor_id="target-editor",
        )
    assert stale.value.payload["current_revision"] == 2
    rolled_back = shared_service.select_version(
        TARGET_WS,
        resource["id"],
        version=1,
        expected_revision=2,
        actor_id="target-editor",
    )
    assert rolled_back["revision"] == 3
    assert shared_service.get(SOURCE_WS, resource["id"])["current_selection"]["version"] == 1


def test_concurrent_selection_cas_has_one_winner(
    shared_service, shared_materials, shared_storage
) -> None:
    resource = _resource(shared_service, shared_materials, shared_storage)
    _new_version(shared_service, shared_materials, shared_storage, resource["id"])

    def select(actor: str):
        try:
            return shared_service.select_version(
                SOURCE_WS,
                resource["id"],
                version=2,
                expected_revision=1,
                actor_id=actor,
            )
        except SharedResourceSelectionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, ("actor-a", "actor-b")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, SharedResourceSelectionConflictError) for result in results) == 1
    assert shared_service.get(SOURCE_WS, resource["id"])["current_selection"]["revision"] == 2


def test_revoke_stops_only_new_pins_and_reshare_restores_access(
    shared_service, shared_materials, shared_storage
) -> None:
    resource = _resource(shared_service, shared_materials, shared_storage)
    shared_service.share(SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="owner")
    version = shared_service.get_version(TARGET_WS, resource["id"], 1)

    revoked = shared_service.revoke(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="owner"
    )
    assert revoked["status"] == "revoked"
    assert shared_service.get(TARGET_WS, resource["id"])["access_status"] == "revoked"
    with pytest.raises(ConflictError, match="revoked"):
        shared_service.resolve_exact_version(
            TARGET_WS,
            resource["id"],
            version=1,
            manifest_hash=version["manifest_hash"],
            for_new_run=True,
        )
    old_pin = shared_service.resolve_exact_version(
        TARGET_WS,
        resource["id"],
        version=1,
        manifest_hash=version["manifest_hash"],
        for_new_run=False,
    )
    assert old_pin["manifest_hash"] == version["manifest_hash"]
    assert old_pin["files"][0]["storage_key"].startswith("shared-resources/")
    with pytest.raises(ConflictError):
        shared_service.select_version(
            TARGET_WS, resource["id"], version=1, expected_revision=1, actor_id="target"
        )

    restored = shared_service.share(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="owner"
    )
    assert restored["duplicate"] is False
    assert restored["grant"]["status"] == "active"


def test_source_material_deletion_does_not_change_published_bytes(
    shared_service, shared_materials, shared_storage
) -> None:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"owned-copy", "a.txt"
    )
    resource = create_resource(shared_service, SOURCE_WS, "material", material_id)
    version = shared_service.get_version(SOURCE_WS, resource["id"], 1)

    shared_materials.delete(SOURCE_WS, material_id)

    resolved = shared_service.resolve_exact_version(
        SOURCE_WS,
        resource["id"],
        version=1,
        manifest_hash=version["manifest_hash"],
        for_new_run=True,
    )
    assert shared_storage.objects[resolved["files"][0]["storage_key"]] == b"owned-copy"
    with pytest.raises(NotFoundError):
        shared_service.create_version(
            SOURCE_WS,
            resource["id"],
            source_type="material",
            source_id=material_id,
            idempotency_key="deleted-source",
            created_by="owner",
        )


def test_logical_delete_enforces_global_selection_and_job_pin_references(
    shared_service, shared_materials, shared_storage, job_db
) -> None:
    resource = _resource(shared_service, shared_materials, shared_storage)
    with pytest.raises(SharedResourceInUseError) as selected:
        shared_service.delete_version(SOURCE_WS, resource["id"], version=1)
    assert selected.value.payload["selection_references"] == 1

    v2 = _new_version(shared_service, shared_materials, shared_storage, resource["id"])
    with job_db.write() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('job-shared-pin', %s, 'test', 'job-shared-pin', 'queued', '{}')",
            (SOURCE_WS,),
        )
        from server.app.jobs.queries.shared_resources.lifecycle import insert_job_pin_tx

        grant_id = str(
            conn.execute(
                "select id from shared_resource_grants where resource_id=%s and workspace_id=%s",
                (resource["id"], SOURCE_WS),
            ).fetchone()["id"]
        )
        insert_job_pin_tx(
            conn,
            job_id="job-shared-pin",
            resource_id=resource["id"],
            version_id=v2["id"],
            manifest_hash=v2["manifest_hash"],
            grant_id=grant_id,
        )
    with pytest.raises(SharedResourceInUseError) as pinned:
        shared_service.delete_version(SOURCE_WS, resource["id"], version=2)
    assert pinned.value.payload["job_pin_references"] == 1

    with job_db.write() as conn:
        conn.execute("delete from jobs where id='job-shared-pin'")
    destination_keys = [
        file["storage_key"]
        for file in shared_service.resolve_exact_version(
            SOURCE_WS,
            resource["id"],
            version=2,
            manifest_hash=v2["manifest_hash"],
            for_new_run=False,
        )["files"]
    ]
    deleted = shared_service.delete_version(SOURCE_WS, resource["id"], version=2)
    assert deleted["physical_objects_deleted"] is False
    assert all(key in shared_storage.objects for key in destination_keys)
    with pytest.raises(NotFoundError):
        shared_service.get_version(SOURCE_WS, resource["id"], 2)


def test_exact_pin_rejects_wrong_hash_and_unauthorized_workspace(
    shared_service, shared_materials, shared_storage
) -> None:
    resource = _resource(shared_service, shared_materials, shared_storage)
    version = shared_service.get_version(SOURCE_WS, resource["id"], 1)
    with pytest.raises(ConflictError, match="hash"):
        shared_service.resolve_exact_version(
            SOURCE_WS,
            resource["id"],
            version=1,
            manifest_hash="0" * 64,
            for_new_run=True,
        )
    with pytest.raises(NotFoundError):
        shared_service.resolve_exact_version(
            THIRD_WS,
            resource["id"],
            version=1,
            manifest_hash=version["manifest_hash"],
            for_new_run=False,
        )


def test_accept_time_pin_rechecks_active_grant_and_job_workspace(
    shared_service, shared_materials, shared_storage, job_db
) -> None:
    from server.app.jobs.queries.shared_resources.lifecycle import insert_job_pin_tx

    resource = _resource(shared_service, shared_materials, shared_storage)
    shared = shared_service.share(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="owner"
    )
    version = shared_service.get_version(TARGET_WS, resource["id"], 1)
    with job_db.write() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('job-wrong-workspace', %s, 'test', 'wrong', 'queued', '{}'),"
            " ('job-target-workspace', %s, 'test', 'target', 'queued', '{}')",
            (SOURCE_WS, TARGET_WS),
        )
        with pytest.raises(ValueError, match="job workspace"):
            insert_job_pin_tx(
                conn,
                job_id="job-wrong-workspace",
                resource_id=resource["id"],
                version_id=version["id"],
                manifest_hash=version["manifest_hash"],
                grant_id=shared["grant"]["id"],
            )
        insert_job_pin_tx(
            conn,
            job_id="job-target-workspace",
            resource_id=resource["id"],
            version_id=version["id"],
            manifest_hash=version["manifest_hash"],
            grant_id=shared["grant"]["id"],
        )

    shared_service.revoke(
        SOURCE_WS, resource["id"], target_workspace_id=TARGET_WS, actor_id="owner"
    )
    with job_db.write() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('job-after-revoke', %s, 'test', 'revoked', 'queued', '{}')",
            (TARGET_WS,),
        )
        with pytest.raises(ValueError, match="active grant"):
            insert_job_pin_tx(
                conn,
                job_id="job-after-revoke",
                resource_id=resource["id"],
                version_id=version["id"],
                manifest_hash=version["manifest_hash"],
                grant_id=shared["grant"]["id"],
            )
