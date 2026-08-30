"""Materials service: presign dedup, complete verification, status machine."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import time

import pytest

from server.app.db.connection import connect_database
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.materials import (
    MaterialInUseError,
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
    return MaterialsService(job_db.dsn_identity, storage)


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
    service = MaterialsService(job_db.dsn_identity, None)

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


def _insert_job_referencing(job_db, material_id: str, *, status: str = "queued") -> str:
    """Insert a job whose input_json is a material item naming material_id."""
    job_id = f"job-{material_id[:8]}-{status}"
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type,"
            " source_id, status, input_json)"
            " values (%s, %s, 'demo_workflow', 'material', %s, %s, %s)",
            (
                job_id,
                WORKSPACE_ID,
                material_id,
                status,
                json.dumps({"type": "material", "material_id": material_id}),
            ),
        )
    return job_id


def test_delete_rejects_material_referenced_by_job(service, storage, job_db) -> None:
    result = service.presign(WORKSPACE_ID, filename="k.txt", size_bytes=2)
    material_id = result["material"]["id"]
    storage_key = f"{WORKSPACE_ID}/{material_id}/k.txt"
    storage.objects[storage_key] = b"ok"
    job_id = _insert_job_referencing(job_db, material_id)

    with pytest.raises(MaterialInUseError, match=job_id):
        service.delete(WORKSPACE_ID, material_id)

    # 对象与行都保留：job 后续 dispatch / 重跑仍可物化。
    assert storage.deleted == []
    assert storage_key in storage.objects
    assert service.get(WORKSPACE_ID, material_id)["id"] == material_id


def test_delete_rejects_material_referenced_by_terminal_job(service, storage, job_db) -> None:
    """v1 任何引用都拒删：终结态 job 的引用同样阻断（质量回放仍要物化）。"""
    result = service.presign(WORKSPACE_ID, filename="l.txt", size_bytes=2)
    material_id = result["material"]["id"]
    _insert_job_referencing(job_db, material_id, status="completed")

    with pytest.raises(MaterialInUseError):
        service.delete(WORKSPACE_ID, material_id)


def test_delete_ignores_unrelated_job_inputs(service, storage, job_db) -> None:
    """其他材料的 job 与非 material 输入不阻断删除。"""
    result = service.presign(WORKSPACE_ID, filename="m.txt", size_bytes=2)
    material_id = result["material"]["id"]
    storage_key = f"{WORKSPACE_ID}/{material_id}/m.txt"
    storage.objects[storage_key] = b"ok"
    _insert_job_referencing(job_db, "other-material")
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type,"
            " source_id, input_json)"
            " values ('job-ref-item', %s, 'demo_workflow', 'ref', 'conn:ext', %s)",
            (WORKSPACE_ID, json.dumps({"type": "ref", "connection_key": "c"})),
        )

    service.delete(WORKSPACE_ID, material_id)

    assert storage.deleted == [storage_key]
    with pytest.raises(NotFoundError):
        service.get(WORKSPACE_ID, material_id)


def test_delete_blocked_by_key_share_sees_committed_reference(service, storage, job_db) -> None:
    """TOCTOU 串行化（run 创建侧先持锁方向）：run 创建对材料行持 FOR KEY
    SHARE（未提交）时，delete 的 FOR UPDATE 阻塞到对方提交；随后引用检查
    看到对方已提交的 job → MaterialInUseError，对象与行都保留。"""
    result = service.presign(WORKSPACE_ID, filename="n.txt", size_bytes=2)
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{material_id}/n.txt"] = b"ok"

    outcome: list[str] = []
    entered = threading.Event()

    def _delete() -> None:
        entered.set()
        try:
            service.delete(WORKSPACE_ID, material_id)
            outcome.append("deleted")
        except MaterialInUseError:
            outcome.append("in-use")
        except Exception as exc:  # 线程内意外失败也要带回主线程定位
            outcome.append(f"error:{exc!r}")

    holder = connect_database(job_db.dsn_identity)
    try:
        with holder:
            # 模拟 run 创建侧：对材料行持 KEY SHARE（未提交）。
            holder.execute("select id from materials where id=%s for key share", (material_id,))
            thread = threading.Thread(target=_delete)
            thread.start()
            assert entered.wait(timeout=5)
            time.sleep(0.5)  # delete 线程应正阻塞在 FOR UPDATE 上
            assert thread.is_alive()
            # 对方提交前，run 创建的引用 job 已落库并提交。
            _insert_job_referencing(job_db, material_id)
        # holder 提交，释放 KEY SHARE
        thread.join(timeout=15)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert outcome == ["in-use"]
    assert storage.deleted == []
    assert service.get(WORKSPACE_ID, material_id)["id"] == material_id
