from __future__ import annotations

import json

import pytest

from server.app.agent_broker.claim_admission import admit_candidate
from server.app.agent_broker.claim_scan import ScanState, WorkerView
from server.app.agent_broker.shared_resource_claim import shared_resource_claim_block
from shared.material_cache import MaterializeError
from shared.protocol import SHARED_RESOURCE_PROTOCOL_VERSION
from shared.resource_manifest import SHARED_RESOURCE_CLAIM_FORMAT
from tests.services.shared_resources.conftest import SOURCE_WS, TARGET_WS
from tests.services.shared_resources.helpers import ready_material
from tests.services.shared_resources.worker_execution_helpers import (
    create_worker_resource_job as _create_job,
)


def _select_v2(shared_service, shared_materials, shared_storage, resource_id: str) -> None:
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
    shared_service.select_version(
        TARGET_WS, resource_id, version=2, expected_revision=1, actor_id="editor"
    )


def test_claim_uses_exact_job_pin_after_switch_and_revoke(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id, job, manifest = _create_job(
        job_db, settings, shared_service, shared_materials, shared_storage
    )
    pinned = job_db.get_job_shared_resource_pins(job["id"])[0]
    _select_v2(shared_service, shared_materials, shared_storage, resource_id)
    shared_service.revoke(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")

    block = shared_resource_claim_block(
        job_db,
        TARGET_WS,
        job,
        manifest,
        storage=shared_storage,
        worker_protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION,
    )

    assert block is not None and block["format"] == SHARED_RESOURCE_CLAIM_FORMAT
    assert len(block["pins"]) == 1
    pin = block["pins"][0]
    assert pin["resource_id"] == resource_id and pin["version"] == 1
    assert pin["manifest_hash"] == pinned["manifest_hash"]
    assert pin["files"][0]["path"] == "rules.txt"
    assert pin["files"][0]["size_bytes"] == len(b"resource-v1")
    assert pin["files"][0]["download_url"].startswith("https://s3.test/download/")
    assert shared_storage.presigned_gets == [pinned["files"][0]["storage_key"]]
    serialized = json.dumps(block)
    assert "storage_key" not in serialized
    assert "grant_id" not in serialized and "binding_revision" not in serialized


def test_old_worker_is_refused_on_the_same_descriptor_snapshot(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    _resource_id, job, manifest = _create_job(
        job_db, settings, shared_service, shared_materials, shared_storage
    )
    with pytest.raises(MaterializeError, match="Worker protocol v7"):
        shared_resource_claim_block(
            job_db,
            TARGET_WS,
            job,
            manifest,
            storage=shared_storage,
            worker_protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION - 1,
        )
    assert shared_storage.presigned_gets == []


def test_claim_admission_holds_resource_node_for_old_worker_only(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    resource_id, job, manifest = _create_job(
        job_db, settings, shared_service, shared_materials, shared_storage
    )
    selected = {
        "workspace_id": TARGET_WS,
        "job_id": job["id"],
        "node_key": "prepare",
        "kind": "code",
        "manifest_json": json.dumps(manifest),
        "definition_json": "{}",
        "job_definition_snapshot_json": job["workflow_definition_snapshot_json"],
    }
    broker = type("Broker", (), {"is_workspace_paused": None})()
    view = WorkerView(
        runtimes=set(),
        models=set(),
        labels={},
        allowed_workspaces=set(),
        code_capacity=1,
        protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION - 1,
    )
    state = ScanState()
    assert admit_candidate(broker, selected, view, state) is None
    assert state.skip_reasons["shared_resource_protocol_too_old"] == 1

    capable = WorkerView(
        runtimes=set(),
        models=set(),
        labels={},
        allowed_workspaces=set(),
        code_capacity=1,
        protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION,
    )
    admitted = admit_candidate(broker, selected, capable, ScanState())
    assert admitted is not None and admitted["shared_resources"] == [resource_id]

    plain = dict(selected)
    plain["manifest_json"] = json.dumps({"node_key": "plain", "shared_resources": []})
    plain["node_key"] = "plain"
    plain["job_definition_snapshot_json"] = json.dumps(
        {"nodes": {"plain": {"shared_resources": []}, "prepare": manifest}}
    )
    assert admit_candidate(broker, plain, view, ScanState()) is not None


def test_manifest_declaration_mismatch_fails_before_presign(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    _resource_id, job, manifest = _create_job(
        job_db, settings, shared_service, shared_materials, shared_storage
    )
    manifest["shared_resources"] = ["different-resource"]
    with pytest.raises(ValueError, match="differs from the job snapshot"):
        shared_resource_claim_block(
            job_db,
            TARGET_WS,
            job,
            manifest,
            storage=shared_storage,
            worker_protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION,
        )
    assert shared_storage.presigned_gets == []
