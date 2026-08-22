"""Materials service: presign dedup, complete verification, status machine."""

from __future__ import annotations

import hashlib
import io

import pytest

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.materials import (
    MaterialsService,
    MaterialStorageUnavailableError,
    MaterialVerificationError,
)
from server.app.storage import ObjectHead

WORKSPACE_ID = "ws-materials"
OTHER_WORKSPACE_ID = "ws-materials-other"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        self.presigned.append((storage_key, expires_seconds))
        return f"https://s3.test/upload/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        if payload is None:
            return None
        return ObjectHead(size_bytes=len(payload))

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
def service(job_db, storage) -> MaterialsService:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Materials', 'demo_workflow') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    return MaterialsService(job_db.path, storage)


def _presign_ready(service: MaterialsService, storage: FakeStorage, payload: bytes) -> dict:
    """Run the full presign → PUT → complete flow against the fake storage."""
    content_hash = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID,
        filename="notes.txt",
        size_bytes=len(payload),
        content_type="text/plain",
        content_hash=content_hash,
        created_by="user-1",
    )
    storage.objects[f"{WORKSPACE_ID}/{content_hash}/notes.txt"] = payload
    return service.complete(WORKSPACE_ID, result["material"]["id"])


def test_presign_creates_uploading_row_and_url(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="a.txt", size_bytes=10, content_hash="hash-a")

    material = result["material"]
    assert material["status"] == "uploading"
    assert material["size_bytes"] == 10
    assert result["upload_url"].startswith("https://s3.test/upload/")
    assert result["deduplicated"] is False
    # Storage key layout: {workspace_id}/{content_hash}/{filename}.
    (presigned_key, expiry) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/hash-a/a.txt"
    assert expiry == 3600


def test_presign_without_hash_uses_material_id_in_key(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="b.bin", size_bytes=3)

    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{result['material']['id']}/b.bin"
    assert result["material"]["content_hash"] == ""


def test_presign_dedups_ready_material_with_same_hash(service, storage) -> None:
    payload = b"hello materials"
    ready = _presign_ready(service, storage, payload)

    again = service.presign(
        WORKSPACE_ID,
        filename="renamed.txt",
        size_bytes=len(payload),
        content_hash=_sha256(payload),
    )

    assert again["deduplicated"] is True
    assert again["upload_url"] is None
    assert again["material"]["id"] == ready["id"]
    assert again["material"]["status"] == "ready"


def test_presign_resets_failed_row_for_retry(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="c.txt", size_bytes=5, content_hash="hash-c")
    with pytest.raises(MaterialVerificationError):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"

    retry = service.presign(WORKSPACE_ID, filename="c.txt", size_bytes=5, content_hash="hash-c")

    assert retry["material"]["id"] == result["material"]["id"]
    assert retry["material"]["status"] == "uploading"
    assert retry["upload_url"] is not None


def test_complete_marks_ready_when_object_matches(service, storage) -> None:
    payload = b"exactly-right"
    result = service.presign(
        WORKSPACE_ID,
        filename="d.txt",
        size_bytes=len(payload),
        content_hash=_sha256(payload),
    )
    storage.objects[f"{WORKSPACE_ID}/{_sha256(payload)}/d.txt"] = payload

    material = service.complete(WORKSPACE_ID, result["material"]["id"])

    assert material["status"] == "ready"
    # Complete is idempotent on an already-ready row.
    assert service.complete(WORKSPACE_ID, result["material"]["id"])["status"] == "ready"


def test_complete_fails_when_object_missing(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="e.txt", size_bytes=4)

    with pytest.raises(MaterialVerificationError, match="missing"):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"


def test_complete_fails_on_size_mismatch(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="f.txt", size_bytes=10)
    storage.objects[f"{WORKSPACE_ID}/{result['material']['id']}/f.txt"] = b"short"

    with pytest.raises(MaterialVerificationError, match="size"):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"


def test_complete_fails_on_hash_mismatch(service, storage) -> None:
    declared = _sha256(b"expected")
    result = service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=7, content_hash=declared)
    # Same length as the declaration, different bytes: size passes, hash fails.
    storage.objects[f"{WORKSPACE_ID}/{declared}/g.txt"] = b"tamperd"

    with pytest.raises(MaterialVerificationError, match="sha256"):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"


def test_complete_rejects_expired_material(service, job_db) -> None:
    result = service.presign(WORKSPACE_ID, filename="h.txt", size_bytes=1)
    with job_db.connect() as conn:
        conn.execute(
            "update materials set status='expired' where id=%s",
            (result["material"]["id"],),
        )

    with pytest.raises(ConflictError):
        service.complete(WORKSPACE_ID, result["material"]["id"])


def test_operations_require_configured_storage(job_db) -> None:
    service = MaterialsService(job_db.path, None)

    with pytest.raises(MaterialStorageUnavailableError):
        service.presign(WORKSPACE_ID, filename="x.txt", size_bytes=1)
    with pytest.raises(MaterialStorageUnavailableError):
        service.delete(WORKSPACE_ID, "missing")


def test_get_scopes_to_workspace(service, job_db) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Other', 'demo_workflow') on conflict(id) do nothing",
            (OTHER_WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="i.txt", size_bytes=1)

    with pytest.raises(NotFoundError):
        service.get(OTHER_WORKSPACE_ID, result["material"]["id"])


def test_list_paginates_newest_first(service, storage) -> None:
    for index in range(3):
        service.presign(WORKSPACE_ID, filename=f"file-{index}.txt", size_bytes=index)

    page = service.list(WORKSPACE_ID, limit=2, offset=0)
    assert page["total"] == 3
    assert len(page["materials"]) == 2
    assert page["materials"][0]["filename"] == "file-2.txt"
    rest = service.list(WORKSPACE_ID, limit=2, offset=2)
    assert [m["filename"] for m in rest["materials"]] == ["file-0.txt"]


def test_delete_removes_object_and_row(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="j.txt", size_bytes=2)
    storage_key = f"{WORKSPACE_ID}/{result['material']['id']}/j.txt"
    storage.objects[storage_key] = b"ok"

    service.delete(WORKSPACE_ID, result["material"]["id"])

    assert storage_key not in storage.objects
    assert storage.deleted == [storage_key]
    with pytest.raises(NotFoundError):
        service.get(WORKSPACE_ID, result["material"]["id"])
