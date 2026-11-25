from __future__ import annotations

import hashlib

from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.materials import MaterialsService
from server.app.services.shared_resources.service import SharedResourcesService
from tests.fakes.storage import FakeObjectStorage


def ready_material(
    materials: MaterialsService,
    storage: FakeObjectStorage,
    workspace_id: str,
    payload: bytes,
    filename: str,
    *,
    declare_hash: bool = True,
) -> str:
    content_hash = hashlib.sha256(payload).hexdigest() if declare_hash else ""
    result = materials.presign(
        workspace_id,
        filename=filename,
        size_bytes=len(payload),
        content_type="text/plain",
        content_hash=content_hash,
    )
    material_id = result["material"]["id"]
    key = f"{workspace_id}/{content_hash or material_id}/v2g0/m_{material_id}/{filename}"
    storage.objects[key] = payload
    return materials.complete(workspace_id, material_id)["id"]


def create_bundle(
    materials: MaterialsService,
    storage: FakeObjectStorage,
    workspace_id: str,
    members: list[tuple[bytes, str]],
    member_hash_flags: list[bool] | None = None,
) -> str:
    bundle_members = []
    hash_flags = member_hash_flags or [True] * len(members)
    for index, ((payload, path), declare_hash) in enumerate(zip(members, hash_flags, strict=True)):
        material_id = ready_material(
            materials,
            storage,
            workspace_id,
            payload,
            f"upload-{index}.txt",
            declare_hash=declare_hash,
        )
        bundle_members.append({"material_id": material_id, "path": path})
    bundle = MaterialBundlesService(materials.connect_source).create(
        workspace_id, name="shared-source-bundle", members=bundle_members
    )
    return str(bundle["id"])


def create_resource(
    service: SharedResourcesService,
    workspace_id: str,
    source_type: str,
    source_id: str,
    token: str = "create-v1",
) -> dict:
    return service.create_resource(
        workspace_id,
        name="Review rules",
        source_type=source_type,
        source_id=source_id,
        idempotency_key=token,
        created_by="editor-1",
    )
