"""Materials service complete(): declaration verification, upload-generation
pinning, and the locked-identity CAS recheck against concurrent retries.

presign/delete 簇在姊妹文件 test_materials.py；共享 fixture 与行级 helper
在 materials_testlib.py。
"""

from __future__ import annotations

import pytest

from server.app.services.job_errors import ConflictError
from server.app.services.materials import MaterialVerificationError
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
_sha256 = materials_testlib.sha256_hex
_insert_material_row = materials_testlib.insert_material_row


def test_complete_after_retry_verifies_current_generation_key(service, storage, job_db) -> None:
    """#1267 codex R4 P1 目标形态：旧代次已 PUT 的字节永远不能被标
    ready——重试提交后 complete 只认当前代次的 key（其对象缺失即失败），
    此前签发的 URL 再写也只能落到死 key。"""
    payload = b"gen-zero-bytes"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="v.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    # 第 0 代 URL 的 PUT 已落地（重试之前）。
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/v.txt"] = payload

    retry = service.presign(
        WORKSPACE_ID, filename="v.txt", size_bytes=len(payload), content_hash=digest
    )
    (retry_key, _) = storage.presigned[1]
    assert retry_key == f"{WORKSPACE_ID}/{digest}/g1/{material_id}/v.txt"
    assert retry["deduplicated"] is False

    # complete 核验当前代次（g1）的 key：对象缺失 → 422 failed，不吃旧代次字节。
    with pytest.raises(MaterialVerificationError, match="missing"):
        service.complete(WORKSPACE_ID, material_id)
    assert service.get(WORKSPACE_ID, material_id)["status"] == "failed"

    # 新代次字节落地后重试 complete（presign 复位 → g2），正常 ready。
    service.presign(WORKSPACE_ID, filename="v.txt", size_bytes=len(payload), content_hash=digest)
    (final_key, _) = storage.presigned[2]
    assert final_key == f"{WORKSPACE_ID}/{digest}/g2/{material_id}/v.txt"
    storage.objects[final_key] = payload
    assert service.complete(WORKSPACE_ID, material_id)["status"] == "ready"


def test_complete_with_expected_generation(service, storage) -> None:
    """#1269 格子②：携带匹配代次的 complete 照常核验落 ready。"""
    payload = b"gen-pinned"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="p.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    assert result["material"]["upload_generation"] == 0
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/p.txt"] = payload

    record = service.complete(WORKSPACE_ID, material_id, expected_upload_generation=0)

    assert record["status"] == "ready"


def test_complete_with_stale_generation_conflicts_without_touching_row(service, storage) -> None:
    """#1269 格子②：携带旧代次的 complete 立即 409——既不做对象 I/O，也不把
    新一轮上传的行误翻 failed（无代次语义下旧 URL 的 complete 会因新 key 对象
    缺失而落 422 failed，踩掉在途的新一轮上传）。"""
    payload = b"stale-gen"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="s.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    # 第 0 代字节已 PUT；随后 presign 重试推进到代次 1（旧 URL 指向死 key）。
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/s.txt"] = payload
    retry = service.presign(
        WORKSPACE_ID, filename="s.txt", size_bytes=len(payload), content_hash=digest
    )
    assert retry["material"]["upload_generation"] == 1

    with pytest.raises(ConflictError, match="superseded"):
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=0)

    row = service.get(WORKSPACE_ID, material_id)
    assert row["status"] == "uploading"
    assert row["upload_generation"] == 1

    # 匹配代次的 complete 正常走完核验；不带代次保持旧行为（锁内复查兜底）。
    storage.objects[f"{WORKSPACE_ID}/{digest}/g1/{material_id}/s.txt"] = payload
    assert (
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)["status"]
        == "ready"
    )


def test_complete_with_stale_generation_conflicts_on_ready_row(service, storage) -> None:
    """#1284：较新代次已 ready 后，迟到的旧代次 complete 必须 409——代次
    比对先于 ready 幂等快路，旧代次不能拿到 200 让调用方误以为已被取代的
    上传被接受；仅同代次的 ready 重放保持幂等 200。"""
    payload = b"ready-then-stale"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="r.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/r.txt"] = payload
    # g0 的 complete 在途期间客户端 presign 重试（行仍 uploading → 推进 g1），
    # 新代次字节落地并完成 → ready @ g1。
    retry = service.presign(
        WORKSPACE_ID, filename="r.txt", size_bytes=len(payload), content_hash=digest
    )
    assert retry["material"]["upload_generation"] == 1
    storage.objects[f"{WORKSPACE_ID}/{digest}/g1/{material_id}/r.txt"] = payload
    assert (
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)["status"]
        == "ready"
    )

    # 迟到的旧代次 complete：契约承诺 409，不得走 ready 幂等快路返回 200。
    with pytest.raises(ConflictError, match="superseded"):
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=0)

    # 同代次重放幂等 200；不带代次保持旧行为。
    assert (
        service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)["status"]
        == "ready"
    )
    assert service.complete(WORKSPACE_ID, material_id)["status"] == "ready"


def test_complete_cas_rejects_key_rebuild_mid_verification(
    service, storage, job_db, monkeypatch
) -> None:
    """#1267 codex R2 P1：verify（事务外）期间并发重试重建了 storage_key，
    complete 必须 409 而不是按旧快照把（尚未上传的）新 key 标 ready。"""
    payload = b"legacy"
    digest = _sha256(payload)
    old_key = f"{WORKSPACE_ID}/{digest}/folder/"
    _insert_material_row(
        job_db,
        material_id="mat-cas",
        content_hash=digest,
        filename="folder/",
        storage_key=old_key,
        size_bytes=len(payload),
    )
    # 旧 key 的字节齐全（verify 按旧 key 会通过），模拟在途 complete。
    storage.objects[old_key] = payload
    real_verify = service._verify_object
    injected: list[bool] = []

    def verify_then_rebuild(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)
        if not injected:
            injected.append(True)
            # 并发 presign 重试：合法新文件名触发 key 重建（#1267 P2 路径）。
            service.presign(
                WORKSPACE_ID, filename="ok.txt", size_bytes=len(payload), content_hash=digest
            )
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_then_rebuild)

    with pytest.raises(ConflictError, match="complete again"):
        service.complete(WORKSPACE_ID, "mat-cas")

    # 没落 ready；行已被重试重置为 uploading 并指向新 key。
    assert service.get(WORKSPACE_ID, "mat-cas")["status"] == "uploading"
    # 明确的失败语义：客户端重调 complete，按新 key 重新核验后正常 ready。
    storage.objects[f"{WORKSPACE_ID}/{digest}/g1/mat-cas/ok.txt"] = payload
    assert service.complete(WORKSPACE_ID, "mat-cas")["status"] == "ready"


def test_complete_cas_rejects_key_rebuild_when_verification_failed(
    service, storage, job_db, monkeypatch
) -> None:
    """CAS 对称面：verify 失败且 key 同时被重建时，不落 failed（行已属于新一轮上传）。"""
    digest = _sha256(b"legacy-fail")
    old_key = f"{WORKSPACE_ID}/{digest}/folder/"
    _insert_material_row(
        job_db,
        material_id="mat-cas-fail",
        content_hash=digest,
        filename="folder/",
        storage_key=old_key,
    )
    real_verify = service._verify_object

    def verify_then_rebuild(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)  # 旧 key 无对象 → failure
        assert outcome is not None
        service.presign(WORKSPACE_ID, filename="ok.txt", size_bytes=1, content_hash=digest)
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_then_rebuild)

    with pytest.raises(ConflictError, match="complete again"):
        service.complete(WORKSPACE_ID, "mat-cas-fail")

    assert service.get(WORKSPACE_ID, "mat-cas-fail")["status"] == "uploading"


def test_complete_cas_rejects_size_change_mid_verification(
    service, storage, job_db, monkeypatch
) -> None:
    """同族存量竞态：key 合法不重建、但重试更新了 size_bytes——verify 按旧
    size 通过也不能落 ready（CAS 覆盖 verify 的全部输入：key + size）。"""
    payload = b"1234"
    digest = _sha256(payload)
    key = f"{WORKSPACE_ID}/{digest}/r.txt"
    _insert_material_row(
        job_db,
        material_id="mat-cas-size",
        content_hash=digest,
        filename="r.txt",
        storage_key=key,
        size_bytes=len(payload),
    )
    storage.objects[key] = payload
    real_verify = service._verify_object

    def verify_then_resize(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)
        service.presign(WORKSPACE_ID, filename="r.txt", size_bytes=9, content_hash=digest)
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_then_resize)

    with pytest.raises(ConflictError, match="complete again"):
        service.complete(WORKSPACE_ID, "mat-cas-size")

    assert service.get(WORKSPACE_ID, "mat-cas-size")["status"] == "uploading"
    assert service.get(WORKSPACE_ID, "mat-cas-size")["size_bytes"] == 9


def test_complete_cas_rejects_same_identity_retry_mid_verification(
    service, storage, job_db, monkeypatch
) -> None:
    """#1267 codex R3 P1：同 key 同大小的重试不改 key/size，只靠代次可见——
    verify 期间的这种重试同样必须让 complete 409。"""
    payload = b"same-identity"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="s.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/s.txt"] = payload
    real_verify = service._verify_object

    def verify_then_retry(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)
        # 并发重试：同 filename/同 size——key 与 size 都不变，只有代次 +1。
        service.presign(
            WORKSPACE_ID, filename="s.txt", size_bytes=len(payload), content_hash=digest
        )
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_then_retry)

    with pytest.raises(ConflictError, match="complete again"):
        service.complete(WORKSPACE_ID, material_id)

    assert service.get(WORKSPACE_ID, material_id)["status"] == "uploading"


def test_complete_does_not_overwrite_concurrent_ready(
    service, storage, job_db, monkeypatch
) -> None:
    """codex R3 P1 第二形态：并发两个 complete 同身份——本方 verify 失败但
    行已被另一方落 ready 时，不得用 failed 覆盖 ready，幂等返回 ready。"""
    payload = b"late-bytes"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="late.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    key = f"{WORKSPACE_ID}/{digest}/g0/{material_id}/late.txt"
    real_verify = service._verify_object
    injected: list[bool] = []

    def verify_fail_then_other_complete_succeeds(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)
        if not injected:
            injected.append(True)
            # 本方核验时对象尚未上传（失败结论）……
            assert outcome is not None
            # ……其间另一个 complete 看到字节已落地并成功落 ready。
            storage.objects[key] = payload
            service.complete(WORKSPACE_ID, material_id)
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_fail_then_other_complete_succeeds)

    record = service.complete(WORKSPACE_ID, material_id)

    assert record["status"] == "ready"
    assert service.get(WORKSPACE_ID, material_id)["status"] == "ready"


def test_complete_rejects_expired_mid_verification(service, storage, job_db, monkeypatch) -> None:
    """锁内复查同时钉住 status：verify 期间被 TTL 翻成 expired 的行不落 ready。"""
    payload = b"expiring"
    digest = _sha256(payload)
    result = service.presign(
        WORKSPACE_ID, filename="x.txt", size_bytes=len(payload), content_hash=digest
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{digest}/g0/{material_id}/x.txt"] = payload
    real_verify = service._verify_object

    def verify_then_expire(storage_arg, row):  # noqa: ANN001, ANN202
        outcome = real_verify(storage_arg, row)
        with job_db.connect() as conn:
            conn.execute("update materials set status='expired' where id=%s", (material_id,))
        return outcome

    monkeypatch.setattr(service, "_verify_object", verify_then_expire)

    with pytest.raises(ConflictError, match="expired"):
        service.complete(WORKSPACE_ID, material_id)

    assert service.get(WORKSPACE_ID, material_id)["status"] == "expired"


def test_complete_marks_ready_when_object_matches(service, storage) -> None:
    payload = b"exactly-right"
    result = service.presign(
        WORKSPACE_ID,
        filename="d.txt",
        size_bytes=len(payload),
        content_hash=_sha256(payload),
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{_sha256(payload)}/g0/{material_id}/d.txt"] = payload

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
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{material_id}/g0/{material_id}/f.txt"] = b"short"

    with pytest.raises(MaterialVerificationError, match="size"):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"


def test_complete_fails_on_hash_mismatch(service, storage) -> None:
    declared = _sha256(b"expected")
    result = service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=7, content_hash=declared)
    # Same length as the declaration, different bytes: size passes, hash fails.
    storage.objects[f"{WORKSPACE_ID}/{declared}/g0/{result['material']['id']}/g.txt"] = b"tamperd"

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


def test_complete_records_head_etag_atomically(service, storage, job_db) -> None:
    """#1279 验收项：complete 成功时把核验 HEAD 的 ETag 与 ready 同事务落库
    （无 hash 行的唯一内容身份）；核验失败/未 complete 的行 etag 保持 ''。"""
    from tests.fakes.storage import etag_of

    payload = b"etag-recorded"
    result = service.presign(WORKSPACE_ID, filename="e.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    assert materials_testlib.object_etag_of(job_db, material_id) == ""
    storage.objects[f"{WORKSPACE_ID}/{material_id}/g0/{material_id}/e.txt"] = payload

    record = service.complete(WORKSPACE_ID, material_id)

    assert record["status"] == "ready"
    assert materials_testlib.object_etag_of(job_db, material_id) == etag_of(payload)

    # 核验失败的行不落 etag。
    bad = service.presign(WORKSPACE_ID, filename="bad.txt", size_bytes=9)
    bad_id = bad["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{bad_id}/g0/{bad_id}/bad.txt"] = b"short"
    with pytest.raises(MaterialVerificationError, match="size"):
        service.complete(WORKSPACE_ID, bad_id)
    assert materials_testlib.object_etag_of(job_db, bad_id) == ""
