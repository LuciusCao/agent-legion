"""#1279 codex r4 P1（恢复面）：ETag 失配行的受控修复与重传愈合链。

覆盖 request_material_reupload（ready → uploading + 清 etag）与后续
presign 重试路径（dedup 不再复用坏行、代次 +1、v2g 新 key、重新签发
URL），以及重传完成后历史引用（按 material_id）消费新字节的愈合语义。
"""

from __future__ import annotations

import pytest

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.material_cache import material_runtime_block
from shared.material_cache import MaterializeError
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
_sha256 = materials_testlib.sha256_hex
_presign_ready = materials_testlib.presign_ready
_storage_key_of = materials_testlib.storage_key_of
_generation_of = materials_testlib.generation_of
_object_etag_of = materials_testlib.object_etag_of


def test_request_reupload_repairs_poisoned_row_and_heals_consumers(
    service, storage, job_db, tmp_path
) -> None:
    """全链：毒化 → 消费侧 412 → 修复 → 重传（dedup 不复用坏行、新代次
    新 key）→ complete → 历史引用消费新字节。"""
    from tests.fakes.storage import etag_of

    payload = b"original-good"
    ready = _presign_ready(service, storage, payload)
    material_id = ready["id"]
    old_key = _storage_key_of(job_db, material_id)
    job = {"id": "job-1", "input_json": {"type": "material", "material_id": material_id}}

    # 修复前：同 hash presign 走 dedup（不签发 URL）——pin 现状语义。
    again = service.presign(
        WORKSPACE_ID, filename="notes.txt", size_bytes=len(payload), content_hash=_sha256(payload)
    )
    assert again["deduplicated"] is True and again["upload_url"] is None

    # complete 后同尺寸静默覆盖（已签发 URL 的 1h 窗口残留场景）→ 412。
    storage.objects[old_key] = b"poisoned-same"  # 与 payload 同长（13 字节）
    with pytest.raises(MaterializeError, match="ETag mismatch"):
        material_runtime_block(job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage)

    # 受控修复：ready → uploading + 清 etag。
    job_db.request_material_reupload(WORKSPACE_ID, material_id)
    assert service.get(WORKSPACE_ID, material_id)["status"] == "uploading"
    assert _object_etag_of(job_db, material_id) == ""

    # 修复后同 hash presign 不再 dedup：代次 +1、v2g 新 key、签发新 URL。
    retry = service.presign(
        WORKSPACE_ID, filename="notes.txt", size_bytes=len(payload), content_hash=_sha256(payload)
    )
    assert retry["deduplicated"] is False and retry["upload_url"] is not None
    assert retry["material"]["upload_generation"] == 1
    new_key = _storage_key_of(job_db, material_id)
    assert new_key != old_key
    assert new_key == f"{WORKSPACE_ID}/{_sha256(payload)}/v2g1/m_{material_id}/notes.txt"

    # 重传正确字节 → complete 落 ready + 新 etag；历史引用（material_id）
    # 消费新字节——愈合语义。
    storage.objects[new_key] = payload
    assert service.complete(WORKSPACE_ID, material_id)["status"] == "ready"
    assert _object_etag_of(job_db, material_id) == etag_of(payload)
    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None and block["material_id"] == material_id


def test_request_reupload_rejects_non_ready_and_unknown(service, storage, job_db) -> None:
    ready = _presign_ready(service, storage, b"x")
    service.delete(WORKSPACE_ID, ready["id"])
    with pytest.raises(NotFoundError):
        job_db.request_material_reupload(WORKSPACE_ID, ready["id"])

    uploading = service.presign(WORKSPACE_ID, filename="u.txt", size_bytes=1)
    with pytest.raises(ConflictError, match="not ready"):
        job_db.request_material_reupload(WORKSPACE_ID, uploading["material"]["id"])


def test_rebind_material_etag_cas(job_db) -> None:
    """重绑 CAS：expected 匹配才落新值；并发改动（expected 不符）不动行。"""
    ready_row = None
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Rebind') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by, object_etag)"
            " values ('mat-rebind', %s, '', 'a.txt', 'text/plain', 3,"
            " %s, 'ready', 'user-1', 'etag-old')",
            (WORKSPACE_ID, f"{WORKSPACE_ID}/mat-rebind/v2g0/m_mat-rebind/a.txt"),
        )
        ready_row = conn.execute(
            "select object_etag from materials where id='mat-rebind'"
        ).fetchone()
    assert ready_row is not None and ready_row["object_etag"] == "etag-old"

    # 扫描面只收有 etag 的行（含本行）。
    rows = job_db.materials_with_object_etag()
    assert [str(r["id"]) for r in rows] == ["mat-rebind"]
    assert str(rows[0]["storage_key"]).endswith("m_mat-rebind/a.txt")

    # CAS 失败：expected 不符 → 不动行。
    assert not job_db.rebind_material_etag(
        "mat-rebind", expected_etag="etag-x", new_etag="etag-new"
    )
    assert _object_etag_of(job_db, "mat-rebind") == "etag-old"
    # CAS 成功。
    assert job_db.rebind_material_etag("mat-rebind", expected_etag="etag-old", new_etag="etag-new")
    assert _object_etag_of(job_db, "mat-rebind") == "etag-new"
    # 重绑后落出扫描面语义不变（仍是带 etag 行）。
    assert [str(r["id"]) for r in job_db.materials_with_object_etag()] == ["mat-rebind"]
