"""#1279 codex r4/r5（恢复面）：ETag 失配行的受控修复与重传愈合链。

覆盖 MaterialsService.request_material_reupload（ready → uploading +
清 etag + 代次 +1 + 轮换 v2g key + 为原行签发受代次保护的重传 URL），
hash / 无 hash 两格统一由原 material_id 愈合，以及重传完成后历史引用
（按 material_id）消费新字节的愈合语义。
"""

from __future__ import annotations

import pytest

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.material_cache import material_runtime_block
from server.app.services.materials import MaterialVerificationError
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
    """全链：毒化 → 消费侧 412 → 修复（原行轮换 + 签发 URL）→ URL 重传 →
    complete（携带轮换后代次）→ ready → 历史引用消费新字节。"""
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

    # 受控修复：ready → uploading + 清 etag + 代次 +1 + 轮换 v2g key +
    # 为原行新 key 签发受代次保护的 PUT URL（r5 P1：不再依赖 presign 分流）。
    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    assert outcome["material_id"] == material_id
    assert outcome["upload_generation"] == 1
    assert service.get(WORKSPACE_ID, material_id)["status"] == "uploading"
    assert _object_etag_of(job_db, material_id) == ""
    repaired_key = _storage_key_of(job_db, material_id)
    assert repaired_key == f"{WORKSPACE_ID}/{_sha256(payload)}/v2g1/m_{material_id}/notes.txt"
    assert repaired_key != old_key
    assert outcome["storage_key"] == repaired_key
    assert outcome["upload_url"] == f"https://s3.test/upload/{repaired_key}"

    # 用修复签发的 URL 重传正确字节（fake：直写对象），complete 携带轮换后
    # 代次 → ready + 新 etag；历史引用（material_id）消费新字节——愈合。
    storage.objects[repaired_key] = payload
    record = service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)
    assert record["status"] == "ready"
    assert _object_etag_of(job_db, material_id) == etag_of(payload)
    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None and block["material_id"] == material_id


def test_hashless_row_heals_via_repair_issued_url(service, storage, job_db, tmp_path) -> None:
    """codex r5 P1 目标格：无 hash 行没有「presign 按 hash 找旧行」的回路
    ——repair 必须为原 material_id 签发重传 URL（presign 只会建新行，历史
    引用永远指着原行）。"""
    from tests.fakes.storage import etag_of

    payload = b"hashless-original"
    result = service.presign(WORKSPACE_ID, filename="h.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    old_key = _storage_key_of(job_db, material_id)
    storage.objects[old_key] = payload
    assert service.complete(WORKSPACE_ID, material_id)["status"] == "ready"
    job = {"id": "job-h", "input_json": {"type": "material", "material_id": material_id}}

    # 同尺寸覆盖毒化 → 412。
    storage.objects[old_key] = b"hashless-poisoned"  # 同长（17 字节）
    with pytest.raises(MaterializeError, match="ETag mismatch"):
        material_runtime_block(job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage)

    # 修复：material_id 不变、代次 +1、轮换 key、签发 URL。
    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    assert outcome["material_id"] == material_id
    assert outcome["upload_generation"] == 1
    new_key = outcome["storage_key"]
    assert new_key != old_key and f"/v2g1/m_{material_id}/" in new_key

    # pin：无 hash 行的 presign 只建新行——修复回路不依赖 presign。
    fresh = service.presign(WORKSPACE_ID, filename="h.txt", size_bytes=len(payload))
    assert fresh["material"]["id"] != material_id

    # 用修复签发的 URL 重传 → complete(代次) → ready；历史引用愈合。
    storage.objects[new_key] = payload
    record = service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)
    assert record["status"] == "ready"
    assert _object_etag_of(job_db, material_id) == etag_of(payload)
    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None and block["material_id"] == material_id

    # 新行只是另一个材料：删除它不影响愈合后的原行。
    service.delete(WORKSPACE_ID, fresh["material"]["id"])
    assert service.get(WORKSPACE_ID, material_id)["status"] == "ready"


def test_bare_complete_cannot_undo_repair_with_poisoned_bytes(service, storage, job_db) -> None:
    """r5 P2 正测试（评审 scratch 场景转正）：无 hash 行被同尺寸覆盖毒化 →
    repair（代次 +1、key 轮换）→ 窗口期内的 bare complete 不得把毒化字节
    重新落 ready：带旧代次 → 409 代次守卫；无代次 → 新 key 缺对象落
    failed（size-only 核验够不着旧 key 的毒化对象）。"""
    payload = b"hashless-original"
    result = service.presign(WORKSPACE_ID, filename="h.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    old_key = _storage_key_of(job_db, material_id)
    storage.objects[old_key] = payload
    assert service.complete(WORKSPACE_ID, material_id)["status"] == "ready"

    # 同尺寸覆盖毒化，然后修复（轮换 key）。
    storage.objects[old_key] = b"hashless-poisoned"  # 同长（17 字节）
    service.request_material_reupload(WORKSPACE_ID, material_id)
    assert _generation_of(job_db, material_id) == 1

    # 带旧代次的 bare complete → 409，行仍是 uploading。
    with pytest.raises(ConflictError, match="superseded"):
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=0)
    assert service.get(WORKSPACE_ID, material_id)["status"] == "uploading"

    # 无代次 bare complete → 新 key 无对象 → 核验失败落 failed，不落 ready。
    with pytest.raises(MaterialVerificationError, match="missing"):
        service.complete(WORKSPACE_ID, material_id)
    row = service.get(WORKSPACE_ID, material_id)
    assert row["status"] == "failed"
    assert _object_etag_of(job_db, material_id) == ""


def test_repair_rerun_reissues_url_without_rotating(service, storage, job_db) -> None:
    """幂等重试：repair → PUT 之间崩溃后重跑 repair——不二次轮换，重签
    现状 key 的 URL（上传在途行重签与首发 URL 同能力，无新风险面）。"""
    ready = _presign_ready(service, storage, b"rerun")
    material_id = ready["id"]

    first = service.request_material_reupload(WORKSPACE_ID, material_id)
    second = service.request_material_reupload(WORKSPACE_ID, material_id)

    assert first["upload_generation"] == 1
    assert second["upload_generation"] == 1
    assert second["storage_key"] == first["storage_key"]
    assert _generation_of(job_db, material_id) == 1


def test_request_reupload_rejects_non_ready_and_unknown(service, storage, job_db) -> None:
    ready = _presign_ready(service, storage, b"x")
    service.delete(WORKSPACE_ID, ready["id"])
    with pytest.raises(NotFoundError):
        service.request_material_reupload(WORKSPACE_ID, ready["id"])

    expired = _presign_ready(service, storage, b"y")
    with job_db.connect() as conn:
        conn.execute("update materials set status='expired' where id=%s", (expired["id"],))
    with pytest.raises(ConflictError, match="expired"):
        service.request_material_reupload(WORKSPACE_ID, expired["id"])


def test_rebind_material_etag_cas(job_db) -> None:
    """重绑 CAS：expected 匹配才落新值；并发改动（expected 不符）不动行。"""
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
