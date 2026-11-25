from __future__ import annotations

from dataclasses import replace

import psycopg
import pytest
from botocore.exceptions import ClientError

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.shared_resources.errors import SharedResourceCopyError
from server.app.services.shared_resources.keys import KEY_PREFIX
from server.app.services.verified_object_copy import VerifiedObjectCopyResult
from tests.services.shared_resources.conftest import SOURCE_WS
from tests.services.shared_resources.helpers import create_bundle, create_resource, ready_material


def test_material_create_is_idempotent_and_versions_are_immutable(
    shared_service, shared_materials, shared_storage, job_db
) -> None:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"immutable-v1", "rules.txt"
    )
    resource = create_resource(shared_service, SOURCE_WS, "material", material_id)

    assert resource["version_count"] == 1
    assert resource["current_selection"]["version"] == 1
    version = shared_service.get_version(SOURCE_WS, resource["id"], 1)
    assert version["file_count"] == 1
    assert version["files"][0]["path"] == "rules.txt"
    assert len(version["manifest_hash"]) == 64
    assert len(version["files"][0]["sha256"]) == 64
    assert version["source"]["materials"][0]["material_id"] == material_id
    assert "storage_key" not in version["files"][0]
    assert len([key for key in shared_storage.objects if key.startswith(KEY_PREFIX)]) == 1

    assert create_resource(shared_service, SOURCE_WS, "material", material_id) == resource
    assert len([key for key in shared_storage.objects if key.startswith(KEY_PREFIX)]) == 1
    with pytest.raises(ConflictError, match="different resource"):
        shared_service.create_resource(
            SOURCE_WS,
            name="Different name",
            source_type="material",
            source_id=material_id,
            idempotency_key="create-v1",
            created_by="editor-1",
        )

    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute(
            "update shared_resource_versions set manifest_hash=%s where id=%s",
            ("0" * 64, version["id"]),
        )
    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute("delete from shared_resource_files where version_id=%s", (version["id"],))


def test_bundle_create_copies_each_path_once_and_supports_new_versions(
    shared_service, shared_materials, shared_storage
) -> None:
    bundle_id = create_bundle(
        shared_materials,
        shared_storage,
        SOURCE_WS,
        [(b"alpha", "a.txt"), (b"beta", "nested/b.txt")],
    )
    resource = create_resource(shared_service, SOURCE_WS, "bundle", bundle_id)

    version = shared_service.get_version(SOURCE_WS, resource["id"], 1)
    assert [file["path"] for file in version["files"]] == ["a.txt", "nested/b.txt"]
    assert version["total_size_bytes"] == len(b"alpha") + len(b"beta")
    assert len([key for key in shared_storage.objects if key.startswith(KEY_PREFIX)]) == 2

    newer_material = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"immutable-v2", "rules-v2.txt"
    )
    v2 = shared_service.create_version(
        SOURCE_WS,
        resource["id"],
        source_type="material",
        source_id=newer_material,
        idempotency_key="create-v2",
        created_by="editor-1",
    )
    assert v2["version"] == 2
    assert [row["version"] for row in shared_service.list_versions(SOURCE_WS, resource["id"])] == [
        2,
        1,
    ]
    assert shared_service.get(SOURCE_WS, resource["id"])["current_selection"]["version"] == 1


def test_missing_declared_hash_is_computed_from_the_conditioned_source(
    shared_service, shared_materials, shared_storage
) -> None:
    material_id = ready_material(
        shared_materials,
        shared_storage,
        SOURCE_WS,
        b"legacy-size-only-material",
        "legacy.txt",
        declare_hash=False,
    )

    resource = create_resource(shared_service, SOURCE_WS, "material", material_id)

    version = shared_service.get_version(SOURCE_WS, resource["id"], 1)
    import hashlib

    assert version["files"][0]["sha256"] == hashlib.sha256(b"legacy-size-only-material").hexdigest()


def test_second_file_copy_failure_is_invisible_and_same_token_retries_new_keys(
    shared_service, shared_materials, shared_storage, job_db, monkeypatch
) -> None:
    bundle_id = create_bundle(
        shared_materials, shared_storage, SOURCE_WS, [(b"first", "a.txt"), (b"second", "b.txt")]
    )
    real_copy = shared_storage.copy_object
    calls = 0

    def fail_second(source_key, destination_key, source_etag=""):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ClientError({"Error": {"Code": "InternalError"}}, "CopyObject")
        real_copy(source_key, destination_key, source_etag=source_etag)

    monkeypatch.setattr(shared_storage, "copy_object", fail_second)
    with pytest.raises(SharedResourceCopyError, match="copy_failed"):
        create_resource(shared_service, SOURCE_WS, "bundle", bundle_id)

    with pytest.raises(NotFoundError):
        shared_service.get(SOURCE_WS, "not-yet-visible")
    assert shared_service.list_resources(SOURCE_WS, limit=50, offset=0)["total"] == 0
    with job_db.read() as conn:
        row = conn.execute("select status, resource_id from shared_resource_versions").fetchone()
    assert row["status"] == "failed"
    resource_id = str(row["resource_id"])
    with pytest.raises(NotFoundError):
        shared_service.get(SOURCE_WS, resource_id)
    assert not [key for key in shared_storage.objects if key.startswith(KEY_PREFIX)]

    monkeypatch.setattr(shared_storage, "copy_object", real_copy)
    resource = create_resource(shared_service, SOURCE_WS, "bundle", bundle_id)
    assert resource["id"] == resource_id
    assert shared_service.get_version(SOURCE_WS, resource_id, 1)["file_count"] == 2
    with job_db.read() as conn:
        generation = conn.execute(
            "select copy_generation from shared_resource_versions where resource_id=%s",
            (resource_id,),
        ).fetchone()["copy_generation"]
    assert generation == 2


def test_independent_manifest_mutation_rejects_forged_helper_success(
    shared_service, shared_materials, shared_storage, monkeypatch
) -> None:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"mutation-check", "mutation.txt"
    )
    from server.app.services.shared_resources import creation as creation_module

    real_copy = creation_module.copy_objects_verified

    def forged_success(storage, specs):
        result = real_copy(storage, specs)
        assert result.ok
        forged = replace(result.copies[0], sha256="0" * 64)
        return VerifiedObjectCopyResult(copies=(forged,))

    monkeypatch.setattr(creation_module, "copy_objects_verified", forged_success)

    with pytest.raises(SharedResourceCopyError, match="verification_failed"):
        create_resource(shared_service, SOURCE_WS, "material", material_id)
    assert shared_service.list_resources(SOURCE_WS, limit=50, offset=0)["total"] == 0


def test_source_change_after_copy_blocks_publication(
    shared_service, shared_materials, shared_storage, job_db, monkeypatch
) -> None:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"snapshot", "snapshot.txt"
    )
    from server.app.services.shared_resources import creation as creation_module

    real_copy = creation_module.copy_objects_verified

    def copy_then_change_source(storage, specs):
        result = real_copy(storage, specs)
        with job_db.write() as conn:
            conn.execute(
                "update materials set object_etag='changed-after-copy' where id=%s", (material_id,)
            )
        return result

    monkeypatch.setattr(creation_module, "copy_objects_verified", copy_then_change_source)

    with pytest.raises(ConflictError, match="source changed"):
        create_resource(shared_service, SOURCE_WS, "material", material_id)
    assert shared_service.list_resources(SOURCE_WS, limit=50, offset=0)["total"] == 0


def test_concurrent_same_token_returns_ready_without_publishing_superseded_generation(
    shared_service, shared_materials, shared_storage, job_db, monkeypatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from server.app.services.shared_resources import creation as creation_module

    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"concurrent-create", "concurrent.txt"
    )
    real_copy = creation_module.copy_objects_verified
    first_started = Event()
    release_first = Event()
    lock = Lock()
    calls = 0

    def gated_copy(storage, specs):
        nonlocal calls
        with lock:
            calls += 1
            current = calls
        if current == 1:
            first_started.set()
            assert release_first.wait(timeout=10)
        return real_copy(storage, specs)

    monkeypatch.setattr(creation_module, "copy_objects_verified", gated_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create_resource, shared_service, SOURCE_WS, "material", material_id)
        assert first_started.wait(timeout=10)
        try:
            second = create_resource(shared_service, SOURCE_WS, "material", material_id)
        finally:
            release_first.set()
        assert first.result(timeout=10) == second

    version = shared_service.get_version(SOURCE_WS, second["id"], 1)
    resolved = shared_service.resolve_exact_version(
        SOURCE_WS,
        second["id"],
        version=1,
        manifest_hash=version["manifest_hash"],
        for_new_run=True,
    )
    assert "/g2/" in resolved["files"][0]["storage_key"]
    assert shared_storage.objects[resolved["files"][0]["storage_key"]] == b"concurrent-create"
    with job_db.read() as conn:
        rows = conn.execute(
            "select status, copy_generation from shared_resource_versions where resource_id=%s",
            (second["id"],),
        ).fetchall()
    assert [(row["status"], row["copy_generation"]) for row in rows] == [("ready", 2)]
