"""Materials service: presign dedup, complete verification, status machine."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from server.app.db.connection import connect_database
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.material_key_validation import (
    InvalidMaterialContentHashError,
    InvalidMaterialFilenameError,
)
from server.app.services.materials import (
    MaterialInUseError,
    MaterialsService,
    MaterialStorageUnavailableError,
    MaterialVerificationError,
)
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.pg_waits import backend_pid, wait_until_blocked_by

FakeStorage = FakeObjectStorage

WORKSPACE_ID = "ws-materials"
OTHER_WORKSPACE_ID = "ws-materials-other"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def service(job_db, storage) -> MaterialsService:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Materials') on conflict(id) do nothing",
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
    digest = _sha256(b"a")
    result = service.presign(WORKSPACE_ID, filename="a.txt", size_bytes=10, content_hash=digest)

    material = result["material"]
    assert material["status"] == "uploading"
    assert material["size_bytes"] == 10
    assert result["upload_url"].startswith("https://s3.test/upload/")
    assert result["deduplicated"] is False
    # Storage key layout: {workspace_id}/{content_hash}/{filename}.
    (presigned_key, expiry) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/a.txt"
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
    digest = _sha256(b"c")
    result = service.presign(WORKSPACE_ID, filename="c.txt", size_bytes=5, content_hash=digest)
    with pytest.raises(MaterialVerificationError):
        service.complete(WORKSPACE_ID, result["material"]["id"])
    assert service.get(WORKSPACE_ID, result["material"]["id"])["status"] == "failed"

    retry = service.presign(WORKSPACE_ID, filename="c.txt", size_bytes=5, content_hash=digest)

    assert retry["material"]["id"] == result["material"]["id"]
    assert retry["material"]["status"] == "uploading"
    assert retry["upload_url"] is not None


@pytest.mark.parametrize("filename", ["folder/", "/"])
def test_presign_rejects_filename_ending_with_slash(service, storage, filename) -> None:
    """#1119：以 / 结尾的 key 被 SeaweedFS 存成目录，下载/备份落不了盘。"""
    with pytest.raises(InvalidMaterialFilenameError, match="must not end with '/'"):
        service.presign(WORKSPACE_ID, filename=filename, size_bytes=0)

    # 拒绝先于一切写：不留行、不签发 URL。
    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


@pytest.mark.parametrize(
    "filename", [".", "..", "./a.txt", "a/../b.txt", "dir/./f.txt", "a/..", "..hidden/ok.txt/../x"]
)
def test_presign_rejects_dot_segments(service, storage, filename) -> None:
    """#1119：. / .. 段在下载时会被路径归一化吃掉（对齐 valid_artifact_name 纪律）。"""
    with pytest.raises(InvalidMaterialFilenameError, match="path segments"):
        service.presign(WORKSPACE_ID, filename=filename, size_bytes=1)

    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


@pytest.mark.parametrize("filename", ["a//b.txt", "/a.txt", "//a.txt", "a///b.txt"])
def test_presign_rejects_empty_segments(service, storage, filename) -> None:
    """#1119 复验 P2：空段下载时被归一化，a//b 与 a/b 落同一文件互相覆盖。"""
    with pytest.raises(InvalidMaterialFilenameError, match="empty path segments"):
        service.presign(WORKSPACE_ID, filename=filename, size_bytes=1)

    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


@pytest.mark.parametrize("filename", ["a\\b.txt", "C:\\data\\f.txt"])
def test_presign_rejects_backslashes(service, storage, filename) -> None:
    r"""#1119 复验 P3：对齐 valid_artifact_name / validate_member_path 的 \ 禁令。"""
    with pytest.raises(InvalidMaterialFilenameError, match="backslash"):
        service.presign(WORKSPACE_ID, filename=filename, size_bytes=1)

    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


@pytest.mark.parametrize("filename", ["a\nb.txt", "a\tb.txt", "a\x7fb.txt", "a\x01b.txt"])
def test_presign_rejects_control_characters(service, storage, filename) -> None:
    """#1119 复验 P3：含换行的 key 会让备份 MARKERS 的逐行表达判失败。"""
    with pytest.raises(InvalidMaterialFilenameError, match="control characters"):
        service.presign(WORKSPACE_ID, filename=filename, size_bytes=1)

    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


@pytest.mark.parametrize(
    "content_hash",
    ["../other", "a/b", "nothex", "g" * 64, "A" * 64, "a" * 63, "a" * 65, ".."],
)
def test_presign_rejects_invalid_content_hash(service, storage, content_hash) -> None:
    """#1267 codex P1：content_hash 同样拼进 storage_key，非空时必须是 64 位
    小写 sha256 hex——否则 '../other' 会签出 {ws}/../other/file.txt 的 key。"""
    with pytest.raises(InvalidMaterialContentHashError, match="sha256"):
        service.presign(WORKSPACE_ID, filename="ok.txt", size_bytes=1, content_hash=content_hash)

    assert storage.presigned == []
    assert service.list(WORKSPACE_ID, limit=10, offset=0)["total"] == 0


def _insert_material_row(
    job_db,
    *,
    material_id: str,
    content_hash: str,
    filename: str,
    storage_key: str,
    size_bytes: int = 1,
) -> None:
    """直接落一条材料行，模拟校验收紧（#1119/#1267）前入库的 legacy 行。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, %s, 'text/plain', %s, %s, 'failed', 'user-1')",
            (material_id, WORKSPACE_ID, content_hash, filename, size_bytes, storage_key),
        )


def _storage_key_of(job_db, material_id: str) -> str:
    with job_db.connect() as conn:
        row = conn.execute(
            "select storage_key from materials where id=%s", (material_id,)
        ).fetchone()
    assert row is not None
    return str(row["storage_key"])


def test_presign_retry_rebuilds_invalid_legacy_key(service, storage, job_db) -> None:
    """#1267 codex P2：收紧前入库的行可能带着非法 key（以 / 结尾等）；用合法
    新文件名重试时必须按当前字段重建 key，不能把旧 key 再次签发。"""
    digest = _sha256(b"legacy")
    _insert_material_row(
        job_db,
        material_id="mat-legacy",
        content_hash=digest,
        filename="folder/",
        storage_key=f"{WORKSPACE_ID}/{digest}/folder/",
    )

    retry = service.presign(WORKSPACE_ID, filename="ok.txt", size_bytes=1, content_hash=digest)

    assert retry["material"]["id"] == "mat-legacy"
    assert retry["material"]["status"] == "uploading"
    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/ok.txt"
    assert _storage_key_of(job_db, "mat-legacy") == presigned_key


def test_presign_retry_keeps_valid_legacy_key(service, storage, job_db) -> None:
    """重试改名但旧 key 合法时保持既有行为：复用原 key，只更新展示名。"""
    digest = _sha256(b"legacy-ok")
    legacy_key = f"{WORKSPACE_ID}/{digest}/old.txt"
    _insert_material_row(
        job_db,
        material_id="mat-legacy-ok",
        content_hash=digest,
        filename="old.txt",
        storage_key=legacy_key,
    )

    retry = service.presign(WORKSPACE_ID, filename="new.txt", size_bytes=1, content_hash=digest)

    assert retry["material"]["id"] == "mat-legacy-ok"
    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == legacy_key
    assert _storage_key_of(job_db, "mat-legacy-ok") == legacy_key
    assert retry["material"]["filename"] == "new.txt"


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
    storage.objects[f"{WORKSPACE_ID}/{digest}/ok.txt"] = payload
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


def _generation_of(job_db, material_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "select upload_generation from materials where id=%s", (material_id,)
        ).fetchone()
    assert row is not None
    return int(row["upload_generation"])


def test_presign_retry_bumps_upload_generation(service, storage, job_db) -> None:
    """#1267 codex R3 P1：每次重试（即使同 key 同大小）都推进上传代次，
    让 uploading→uploading 的重试对 complete 的锁内复查可见。"""
    digest = _sha256(b"gen")
    result = service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=1, content_hash=digest)
    material_id = result["material"]["id"]
    assert _generation_of(job_db, material_id) == 0

    service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=1, content_hash=digest)
    assert _generation_of(job_db, material_id) == 1
    service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=1, content_hash=digest)
    assert _generation_of(job_db, material_id) == 2


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
    storage.objects[f"{WORKSPACE_ID}/{digest}/s.txt"] = payload
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
    key = f"{WORKSPACE_ID}/{digest}/late.txt"
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
    storage.objects[f"{WORKSPACE_ID}/{digest}/x.txt"] = payload
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


def test_presign_accepts_nested_relative_path(service, storage) -> None:
    """中间带 / 的相对路径合法：材料 key 本就是多段路径，只禁目录形态。"""
    digest = _sha256(b"nested")
    result = service.presign(
        WORKSPACE_ID, filename="dir/sub/file.txt", size_bytes=1, content_hash=digest
    )

    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/dir/sub/file.txt"
    assert result["material"]["filename"] == "dir/sub/file.txt"


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
            "insert into workspaces(id, name) values (%s, 'Other') on conflict(id) do nothing",
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
            "insert into jobs(id, workspace_id, source_type,"
            " source_id, status, input_json)"
            " values (%s, %s, 'material', %s, %s, %s)",
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
            "insert into jobs(id, workspace_id, source_type,"
            " source_id, input_json)"
            " values ('job-ref-item', %s, 'ref', 'conn:ext', %s)",
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
            # delete 线程应正阻塞在 FOR UPDATE 上：以 pg_blocking_pids 观测为准。
            wait_until_blocked_by(backend_pid(holder), thread=thread)
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
