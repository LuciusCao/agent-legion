"""Material bundles service (#156): manifest CRUD, validation, delete guards."""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.material_bundles import (
    MAX_BUNDLE_MEMBERS,
    BundleInUseError,
    MaterialBundlesService,
    validate_member_path,
)
from server.app.services.materials import MaterialInUseError, MaterialsService
from server.app.storage import ObjectHead

WORKSPACE_ID = "ws-bundles"
OTHER_WORKSPACE_ID = "ws-bundles-other"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def materials(job_db, storage) -> MaterialsService:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Bundles', 'demo_workflow'), (%s, 'Other', 'demo_workflow')"
            " on conflict(id) do nothing",
            (WORKSPACE_ID, OTHER_WORKSPACE_ID),
        )
    return MaterialsService(job_db.path, storage)


@pytest.fixture
def bundles(materials) -> MaterialBundlesService:
    return MaterialBundlesService(materials.database_dsn)


def _ready_material(
    materials: MaterialsService, storage: FakeStorage, payload: bytes, filename: str
) -> str:
    content_hash = _sha256(payload)
    result = materials.presign(
        WORKSPACE_ID,
        filename=filename,
        size_bytes=len(payload),
        content_type="text/plain",
        content_hash=content_hash,
    )
    storage.objects[f"{WORKSPACE_ID}/{content_hash}/{filename}"] = payload
    return materials.complete(WORKSPACE_ID, result["material"]["id"])["id"]


def _two_member_bundle(bundles, materials, storage, name: str = "folder") -> dict:
    first = _ready_material(materials, storage, b"alpha", "a.txt")
    second = _ready_material(materials, storage, b"beta-content", "b.txt")
    return bundles.create(
        WORKSPACE_ID,
        name=name,
        members=[
            {"material_id": first, "path": "a.txt"},
            {"material_id": second, "path": "sub/b.txt"},
        ],
    )


def test_create_records_manifest_with_totals(bundles, materials, storage) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)

    assert bundle["name"] == "folder"
    assert bundle["file_count"] == 2
    assert bundle["total_size_bytes"] == len(b"alpha") + len(b"beta-content")
    assert [member["path"] for member in bundle["members"]] == ["a.txt", "sub/b.txt"]
    assert bundle["members"][0]["status"] == "ready"
    # The manifest exposes member material ids, never storage keys.
    assert "storage_key" not in json.dumps(bundle)


def test_create_rejects_empty_name_and_members(bundles, materials, storage) -> None:
    material_id = _ready_material(materials, storage, b"x", "x.txt")
    with pytest.raises(InvalidOperationError, match="name"):
        bundles.create(WORKSPACE_ID, name="  ", members=[{"material_id": material_id, "path": "x"}])
    with pytest.raises(InvalidOperationError, match="at least one member"):
        bundles.create(WORKSPACE_ID, name="b", members=[])
    with pytest.raises(InvalidOperationError, match="limit"):
        bundles.create(
            WORKSPACE_ID,
            name="b",
            members=[
                {"material_id": material_id, "path": f"f-{i}.txt"}
                for i in range(MAX_BUNDLE_MEMBERS + 1)
            ],
        )


def test_create_rejects_unknown_or_unready_member(bundles, materials, storage) -> None:
    with pytest.raises(NotFoundError, match="mat-missing"):
        bundles.create(
            WORKSPACE_ID, name="b", members=[{"material_id": "mat-missing", "path": "a.txt"}]
        )
    result = materials.presign(WORKSPACE_ID, filename="u.txt", size_bytes=3)
    with pytest.raises(InvalidOperationError, match="not ready"):
        bundles.create(
            WORKSPACE_ID,
            name="b",
            members=[{"material_id": result["material"]["id"], "path": "u.txt"}],
        )


def test_create_rejects_duplicate_paths(bundles, materials, storage) -> None:
    material_id = _ready_material(materials, storage, b"dup", "d.txt")
    with pytest.raises(InvalidOperationError, match="unique"):
        bundles.create(
            WORKSPACE_ID,
            name="b",
            members=[
                {"material_id": material_id, "path": "same.txt"},
                {"material_id": material_id, "path": "same.txt"},
            ],
        )


@pytest.mark.parametrize(
    "path",
    ["", "/abs.txt", "a/../b.txt", "a//b.txt", "a/./b.txt", "back\\slash.txt"],
)
def test_validate_member_path_rejects_unsafe(path: str) -> None:
    with pytest.raises(InvalidOperationError):
        validate_member_path(path)


@pytest.mark.parametrize("path", ["line\nbreak.txt", "tab\tname.txt", "bell\x07.txt"])
def test_validate_member_path_rejects_control_characters(path: str) -> None:
    """控制字符会让 manifest 哈希的 TAB/LF 拼接编码产生歧义（#156）。"""
    with pytest.raises(InvalidOperationError, match="control"):
        validate_member_path(path)


def test_validate_member_path_strips_surrounding_slashes() -> None:
    assert validate_member_path(" sub/dir/file.txt ") == "sub/dir/file.txt"


def test_get_scopes_to_workspace(bundles, materials, storage) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)

    assert bundles.get(WORKSPACE_ID, bundle["id"])["id"] == bundle["id"]
    with pytest.raises(NotFoundError):
        bundles.get(OTHER_WORKSPACE_ID, bundle["id"])


def test_list_only_returns_own_workspace(bundles, materials, storage) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)

    listed = bundles.list(WORKSPACE_ID, limit=10, offset=0)
    assert listed["total"] == 1
    assert listed["bundles"][0]["id"] == bundle["id"]
    assert "members" not in listed["bundles"][0]
    assert bundles.list(OTHER_WORKSPACE_ID, limit=10, offset=0)["total"] == 0


def test_delete_removes_manifest_but_not_members(bundles, materials, storage) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)
    member_id = bundle["members"][0]["material_id"]

    bundles.delete(WORKSPACE_ID, bundle["id"])

    with pytest.raises(NotFoundError):
        bundles.get(WORKSPACE_ID, bundle["id"])
    assert materials.get(WORKSPACE_ID, member_id)["id"] == member_id


def _insert_job_referencing_bundle(job_db, bundle_id: str) -> str:
    job_id = f"job-bundle-{bundle_id[:8]}"
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type,"
            " source_id, status, input_json)"
            " values (%s, %s, 'demo_workflow', 'bundle', %s, 'queued', %s)",
            (
                job_id,
                WORKSPACE_ID,
                bundle_id,
                json.dumps({"type": "bundle", "bundle_id": bundle_id}),
            ),
        )
    return job_id


def test_delete_rejects_bundle_referenced_by_job(bundles, materials, storage, job_db) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)
    job_id = _insert_job_referencing_bundle(job_db, bundle["id"])

    with pytest.raises(BundleInUseError, match=job_id):
        bundles.delete(WORKSPACE_ID, bundle["id"])

    assert bundles.get(WORKSPACE_ID, bundle["id"])["id"] == bundle["id"]


def test_materials_delete_rejects_bundle_member(bundles, materials, storage) -> None:
    bundle = _two_member_bundle(bundles, materials, storage)
    member_id = bundle["members"][0]["material_id"]

    with pytest.raises(MaterialInUseError, match=bundle["id"]):
        materials.delete(WORKSPACE_ID, member_id)

    # 删掉 bundle 后成员恢复可删。
    bundles.delete(WORKSPACE_ID, bundle["id"])
    materials.delete(WORKSPACE_ID, member_id)
    with pytest.raises(NotFoundError):
        materials.get(WORKSPACE_ID, member_id)
