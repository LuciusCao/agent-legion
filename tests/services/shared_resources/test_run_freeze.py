from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.jobs.job_id_encoding import encode_job_id, encode_resource_job_id
from server.app.jobs.storage_layout import job_storage_dir
from server.app.routes.job_view_contracts import JobDetailResponse
from server.app.routes.run_contracts import RunDetailResponse
from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_queries import JobQueryService
from server.app.services.node_codes import NodeCodeService
from server.app.services.quality_replay_setup import QualityReplaySetup
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.run_service import RunService
from server.app.services.shared_resources.errors import SharedResourceInUseError
from server.app.services.workflow_draft_publish import publish_workflow_draft
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflows.definition import (
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.shared_resource_runtime import (
    materialize_resource_pins,
    prefetch_shared_resource_block,
)
from server.app.workflows.shared_resources import public_resource_pins
from shared.material_cache import MaterializeError, cache_file_path
from tests.services.shared_resources.conftest import SOURCE_WS, TARGET_WS
from tests.services.shared_resources.helpers import create_resource, ready_material


def _publish(job_db, resource_id: str, workspace_id: str = TARGET_WS):
    definition = workflow_definition_from_mapping(
        {
            "key": workspace_id,
            "label": "Target",
            "nodes": {
                "draft": {
                    "type": "code",
                    "capability": "draft",
                    "shared_resources": [resource_id],
                }
            },
        }
    )
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)
    return definition


def _resource(shared_service, shared_materials, shared_storage) -> str:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"resource-v1", "rules.txt"
    )
    resource = create_resource(shared_service, SOURCE_WS, "material", material_id)
    resource_id = str(resource["id"])
    shared_service.share(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")
    return resource_id


def _v2(shared_service, shared_materials, shared_storage, resource_id: str) -> None:
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"resource-v2", "rules-v2.txt"
    )
    shared_service.create_version(
        SOURCE_WS,
        resource_id,
        source_type="material",
        source_id=material_id,
        idempotency_key="create-v2",
        created_by="owner",
    )


def _select(
    shared_service, resource_id: str, version: int, revision: int, workspace_id: str = TARGET_WS
) -> None:
    shared_service.select_version(
        workspace_id,
        resource_id,
        version=version,
        expected_revision=revision,
        actor_id="editor",
    )


def _service(job_db, settings, shared_materials) -> RunService:
    return RunService(job_db, settings, materials_service=shared_materials)


def _item(shared_materials, shared_storage) -> dict:
    material_id = ready_material(shared_materials, shared_storage, TARGET_WS, b"item", "item.txt")
    return {"type": "material", "material_id": material_id}


def test_draft_unrelated_edit_preserves_declaration_and_run_pin(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    item = _item(shared_materials, shared_storage)
    codes = NodeCodeService(job_db.dsn_identity)
    codes.save_draft(
        TARGET_WS,
        TARGET_WS,
        "draft",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(TARGET_WS, TARGET_WS, "draft")
    definition = workflow_definition_from_mapping(
        {
            "key": TARGET_WS,
            "label": "Target",
            "nodes": {
                "draft": {
                    "type": "code",
                    "capability": "draft",
                    "shared_resources": [resource_id],
                }
            },
        }
    )
    ok, errors = publish_workflow_draft(job_db, TARGET_WS, definition_to_yaml(definition))
    assert (ok, errors) == (True, [])
    active = job_db.get_active_workflow_revision(TARGET_WS, TARGET_WS)
    edited = replace(
        workflow_definition_from_dict(json.loads(active["definition_json"])),
        label="Unrelated label edit",
    )

    ok, errors = publish_workflow_draft(job_db, TARGET_WS, definition_to_yaml(edited))
    assert (ok, errors) == (True, [])
    created = _service(job_db, settings, shared_materials).create_run(
        TARGET_WS, workflow_key=TARGET_WS, items=[item]
    )
    pins = job_db.get_job_shared_resource_pins(created["job_ids"][0])
    assert [pin["version"] for pin in pins] == [1]
    active = job_db.get_active_workflow_revision(TARGET_WS, TARGET_WS)
    republished = workflow_definition_from_dict(json.loads(active["definition_json"]))
    assert republished.label == "Unrelated label edit"
    assert republished.nodes["draft"].shared_resources == (resource_id,)


def test_sync_upgrade_switchback_dedup_and_detail_contract(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _v2(shared_service, shared_materials, shared_storage, resource_id)
    _publish(job_db, resource_id)
    service = _service(job_db, settings, shared_materials)
    item = _item(shared_materials, shared_storage)

    first = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=[item])
    duplicate = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=[item])
    assert duplicate["run"]["id"] == first["run"]["id"]
    assert duplicate["created_count"] == 0 and duplicate["job_ids"] == []

    _select(shared_service, resource_id, 2, 1)
    second = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=[item])
    _select(shared_service, resource_id, 1, 2)
    third = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=[item])

    assert len({first["run"]["id"], second["run"]["id"], third["run"]["id"]}) == 3
    assert len({*first["job_ids"], *second["job_ids"], *third["job_ids"]}) == 3
    versions = [
        job_db.get_job_shared_resource_pins(job_id)[0]["version"]
        for job_id in (*first["job_ids"], *second["job_ids"], *third["job_ids"])
    ]
    assert versions == [1, 2, 1]
    raw = job_db.get_run(first["run"]["id"])
    raw_freeze = json.loads(raw["frozen_pins_json"])["shared_resources"]
    assert raw_freeze["pins"][0]["binding_revision"] == 1
    assert len(raw_freeze["pins_hash"]) == 64
    assert "shared_resources" not in first["run"]["frozen_pins"]

    detail = RunDetailResponse(**service.get_run(TARGET_WS, first["run"]["id"]))
    serialized = detail.model_dump_json()
    assert '"version":1' in serialized and '"sha256"' in serialized
    assert "binding_revision" not in serialized and "storage_key" not in serialized

    query = JobQueryService(
        job_db, settings, WorkspaceExecutionConfigurationService(job_db, settings)
    )
    job_detail = JobDetailResponse(**query.detail(first["job_ids"][0]))
    assert job_detail.shared_resources[0].version == 1
    assert "storage_key" not in job_detail.model_dump_json()


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_long_identity_resource_job_ids_are_bounded_unique_and_materialized(
    mode,
    job_db,
    settings,
    shared_service,
    shared_materials,
    shared_storage,
) -> None:
    workspace_id = "w" * 64
    token = "t" * 64
    job_db.create_workspace("Long workspace", workspace_id=workspace_id)
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    shared_service.share(SOURCE_WS, resource_id, target_workspace_id=workspace_id, actor_id="owner")
    _v2(shared_service, shared_materials, shared_storage, resource_id)
    _publish(job_db, resource_id, workspace_id)
    material_id = ready_material(
        shared_materials, shared_storage, workspace_id, b"long-item", "long.txt"
    )
    service = _service(job_db, settings, shared_materials)
    feeder = RunBatchFeeder(job_db, settings=settings, materials_service=shared_materials)

    def create(items):
        if mode == "sync":
            return service.create_run(workspace_id, workflow_key=workspace_id, items=items)
        accepted = service.create_run_async(workspace_id, workflow_key=workspace_id, items=items)
        while feeder.consume_once():
            pass
        return accepted

    item = {"type": "material", "material_id": material_id, "client_token": token}
    first = create([item])
    first_jobs = job_db.list_jobs(workspace_id, run_id=first["run"]["id"])
    duplicate = create([item])
    assert duplicate["run"]["id"] == first["run"]["id"]
    assert len(job_db.list_jobs(workspace_id, run_id=first["run"]["id"])) == 1

    _select(shared_service, resource_id, 2, 1, workspace_id)
    second = create([item])
    _select(shared_service, resource_id, 1, 2, workspace_id)
    rollback = create([item])
    other_item = {**item, "client_token": "u" * 64}
    other = create([other_item])

    runs = [first, second, rollback, other]
    run_ids = {result["run"]["id"] for result in runs}
    job_ids = {
        str(job["id"])
        for result in runs
        for job in job_db.list_jobs(workspace_id, run_id=result["run"]["id"])
    }
    assert len(run_ids) == 4
    assert len(job_ids) == 4
    assert len(first_jobs) == 1
    assert [
        job_db.get_job_shared_resource_pins(
            str(job_db.list_jobs(workspace_id, run_id=result["run"]["id"])[0]["id"])
        )[0]["version"]
        for result in (first, second, rollback)
    ] == [1, 2, 1]
    for job_id in job_ids:
        assert len(job_id.encode("utf-8")) <= 255
        assert job_storage_dir(job_db.jobs_dir, workspace_id, job_id).is_dir()
    assert encode_resource_job_id(workspace_id, workspace_id, "source", "") == encode_job_id(
        workspace_id, workspace_id, "source"
    )


def test_async_accept_uses_original_snapshot_after_upgrade_revoke_and_delete_attempt(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _v2(shared_service, shared_materials, shared_storage, resource_id)
    _publish(job_db, resource_id)
    service = _service(job_db, settings, shared_materials)
    item = _item(shared_materials, shared_storage)

    accepted = service.create_run_async(TARGET_WS, workflow_key=TARGET_WS, items=[item])
    run_id = accepted["run"]["id"]
    assert accepted["run"]["shared_resources"][0]["version"] == 1
    _select(shared_service, resource_id, 2, 1)
    with pytest.raises(SharedResourceInUseError) as in_use:
        shared_service.delete_version(SOURCE_WS, resource_id, version=1)
    assert in_use.value.payload["job_pin_references"] == 1
    shared_service.revoke(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")

    feeder = RunBatchFeeder(job_db, settings=settings, materials_service=shared_materials)
    while feeder.consume_once():
        pass
    jobs = job_db.list_jobs(TARGET_WS, run_id=run_id)
    assert len(jobs) == 1
    pins = job_db.get_job_shared_resource_pins(str(jobs[0]["id"]))
    assert pins[0]["version"] == 1
    with pytest.raises(InvalidOperationError, match="unavailable or revoked"):
        service.create_run_async(TARGET_WS, workflow_key=TARGET_WS, items=[item])


def test_quality_replay_copies_original_pin_after_revoke(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    definition = _publish(job_db, resource_id)
    service = _service(job_db, settings, shared_materials)
    created = service.create_run(
        TARGET_WS, workflow_key=TARGET_WS, items=[_item(shared_materials, shared_storage)]
    )
    original = job_db.get_job(created["job_ids"][0])
    shared_service.revoke(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")

    copy_id = QualityReplaySetup(job_db, None).build_copy_job(
        TARGET_WS,
        {"id": "replay-item"},
        original,
        definition,
        definition.nodes["draft"],
        "replay-shared-resource",
    )
    assert job_db.get_job_shared_resource_pins(copy_id)[0]["version"] == 1
    copy_job = job_db.get_job(copy_id)
    replay_run = job_db.get_run(copy_job["run_id"])
    assert json.loads(replay_run["frozen_pins_json"])["shared_resources"]["pins"][0]["version"] == 1


def test_host_dispatch_materializes_only_the_old_job_pin(
    job_db, settings, shared_service, shared_materials, shared_storage, tmp_path
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _v2(shared_service, shared_materials, shared_storage, resource_id)
    _publish(job_db, resource_id)
    created = _service(job_db, settings, shared_materials).create_run(
        TARGET_WS, workflow_key=TARGET_WS, items=[_item(shared_materials, shared_storage)]
    )
    job = job_db.get_job(created["job_ids"][0])
    run = job_db.get_run(created["run"]["id"])
    _select(shared_service, resource_id, 2, 1)
    shared_service.revoke(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")
    executor = SimpleNamespace(
        job_db=job_db,
        _materials_cache_root=tmp_path,
        _object_store=lambda: shared_storage,
    )
    context = SimpleNamespace(job=job, node_key="draft")
    block = prefetch_shared_resource_block(executor, context, run)
    local = block[resource_id]["files"][0]["local_path"]
    assert block[resource_id]["version"] == 1
    assert Path(local).read_bytes() == b"resource-v1"
    assert "storage_key" not in json.dumps(block)
    assert (
        prefetch_shared_resource_block(
            executor, SimpleNamespace(job=job, node_key="undeclared"), run
        )
        == {}
    )


def test_multi_resource_materialization_pins_the_whole_collection_during_eviction(
    shared_storage, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES", "5")
    expected = {"resource-a": b"aaaa", "resource-b": b"bbbb"}
    pins = []
    for resource_id, payload in expected.items():
        digest = hashlib.sha256(payload).hexdigest()
        key = f"shared-resources/{resource_id}/object"
        shared_storage.objects[key] = payload
        pins.append(
            {
                "resource_id": resource_id,
                "version": 1,
                "manifest_hash": digest,
                "source": {},
                "files": [
                    {
                        "path": "rules.txt",
                        "sha256": digest,
                        "size_bytes": len(payload),
                        "content_type": "text/plain",
                        "storage_key": key,
                    }
                ],
            }
        )
    cache_root = tmp_path / "collection-cache"

    result = materialize_resource_pins(pins, cache_root, shared_storage)
    for resource_id, payload in expected.items():
        local = Path(result[resource_id]["files"][0]["local_path"])
        assert local.is_file() and local.read_bytes() == payload
        assert (
            hashlib.sha256(local.read_bytes()).hexdigest()
            == pins[0 if resource_id == "resource-a" else 1]["files"][0]["sha256"]
        )
    assert shared_storage.opened == 2

    cached = materialize_resource_pins(pins, cache_root, shared_storage)
    assert shared_storage.opened == 2
    for resource_id, payload in expected.items():
        assert Path(cached[resource_id]["files"][0]["local_path"]).read_bytes() == payload


def test_materialization_fails_on_missing_or_changed_bytes(
    job_db, settings, shared_service, shared_materials, shared_storage, tmp_path
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _publish(job_db, resource_id)
    created = _service(job_db, settings, shared_materials).create_run(
        TARGET_WS, workflow_key=TARGET_WS, items=[_item(shared_materials, shared_storage)]
    )
    pins = job_db.get_job_shared_resource_pins(created["job_ids"][0])
    storage_key = pins[0]["files"][0]["storage_key"]
    original = shared_storage.objects.pop(storage_key)
    with pytest.raises(MaterializeError, match="failed to materialize"):
        materialize_resource_pins(pins, tmp_path / "missing", shared_storage)

    shared_storage.objects[storage_key] = b"resource-XX"
    with pytest.raises(MaterializeError, match="sha256|hash/size"):
        materialize_resource_pins(pins, tmp_path / "changed", shared_storage)

    shared_storage.objects[storage_key] = original
    cache_root = tmp_path / "poisoned"
    cached = cache_file_path(cache_root, pins[0]["files"][0]["sha256"])
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"resource-XX")
    with pytest.raises(MaterializeError, match="hash/size mismatch"):
        materialize_resource_pins(pins, cache_root, shared_storage)


def test_pin_insert_failure_rolls_back_job_and_empty_run(
    job_db,
    settings,
    shared_service,
    shared_materials,
    shared_storage,
    monkeypatch,
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _publish(job_db, resource_id)
    service = _service(job_db, settings, shared_materials)

    def fail_pin(*args, **kwargs):
        raise RuntimeError("injected pin failure")

    monkeypatch.setattr(
        "server.app.jobs.queries.shared_resources.job_bulk_pins.insert_job_pin_tx", fail_pin
    )
    with pytest.raises(RuntimeError, match="injected pin failure"):
        service.create_run(
            TARGET_WS, workflow_key=TARGET_WS, items=[_item(shared_materials, shared_storage)]
        )
    assert service.list_runs(TARGET_WS) == []
    assert job_db.list_jobs(TARGET_WS) == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_all_duplicate_resource_resubmission_heals_failed_run_with_pins_digest(
    mode,
    job_db,
    settings,
    shared_service,
    shared_materials,
    shared_storage,
) -> None:
    resource_id = _resource(shared_service, shared_materials, shared_storage)
    _publish(job_db, resource_id)
    service = _service(job_db, settings, shared_materials)
    items = []
    for index in range(2):
        material_id = ready_material(
            shared_materials,
            shared_storage,
            TARGET_WS,
            f"item-{index}".encode(),
            f"item-{index}.txt",
        )
        items.append({"type": "material", "material_id": material_id})
    if mode == "sync":
        first = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=items)
    else:
        first = service.create_run_async(TARGET_WS, workflow_key=TARGET_WS, items=items)
        feeder = RunBatchFeeder(job_db, settings=settings, materials_service=shared_materials)
        while feeder.consume_once():
            pass
    run_id = first["run"]["id"]
    original_freeze = json.loads(job_db.get_run(run_id)["frozen_pins_json"])
    with job_db.connect() as conn:
        conn.execute(
            "update runs set status='failed', error_message='partway', created_count=1 where id=%s",
            (run_id,),
        )
        if mode == "async":
            conn.execute(
                "update run_batches set status='failed', error_message='partway' where run_id=%s",
                (run_id,),
            )

    if mode == "sync":
        result = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=items)
        assert result["created_count"] == 0 and result["job_ids"] == []
        assert result["skipped_count"] == 2
    else:
        result = service.create_run_async(TARGET_WS, workflow_key=TARGET_WS, items=items)
        assert result["batch"]["status"] == "pending"
    assert result["run"]["id"] == run_id
    assert result["run"]["status"] == "created"
    assert result["run"]["error_message"] == ""
    assert result["run"]["created_count"] == 2
    healed = job_db.get_run(run_id)
    assert healed["status"] == "created" and healed["error_message"] == ""
    assert healed["created_count"] == 2
    assert json.loads(healed["frozen_pins_json"]) == original_freeze


def test_public_job_pin_projection_never_leaks_storage_key() -> None:
    pins = [
        {
            "resource_id": "r",
            "name": "R",
            "version": 1,
            "manifest_hash": "m",
            "source": {},
            "files": [
                {
                    "path": "a",
                    "sha256": "s",
                    "size_bytes": 1,
                    "content_type": "",
                    "storage_key": "secret-key",
                }
            ],
        }
    ]
    assert "secret-key" not in json.dumps(public_resource_pins(pins))
