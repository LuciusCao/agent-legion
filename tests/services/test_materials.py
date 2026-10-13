"""Materials service: presign dedup/retry, key validation, delete guards.

complete() 的核验/代次/CAS 竞态簇在姊妹文件 test_materials_complete.py；
共享 fixture 与行级 helper 在 materials_testlib.py。
"""

from __future__ import annotations

import json
import threading

import pytest

from server.app.db.connection import connect_database
from server.app.services.job_errors import NotFoundError
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
from tests.helpers.pg_waits import backend_pid, wait_until_blocked_by
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
OTHER_WORKSPACE_ID = materials_testlib.OTHER_WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
_sha256 = materials_testlib.sha256_hex
_presign_ready = materials_testlib.presign_ready
_insert_material_row = materials_testlib.insert_material_row
_storage_key_of = materials_testlib.storage_key_of
_generation_of = materials_testlib.generation_of


def test_presign_creates_uploading_row_and_url(service, storage) -> None:
    digest = _sha256(b"a")
    result = service.presign(WORKSPACE_ID, filename="a.txt", size_bytes=10, content_hash=digest)

    material = result["material"]
    assert material["status"] == "uploading"
    assert material["size_bytes"] == 10
    assert result["upload_url"].startswith("https://s3.test/upload/")
    assert result["deduplicated"] is False
    # Storage key layout: {workspace_id}/{content_hash}/g{upload_generation}/{material_id}/{filename}.
    (presigned_key, expiry) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/v2g0/m_{material['id']}/a.txt"
    assert expiry == 3600


def test_presign_without_hash_uses_material_id_in_key(service, storage) -> None:
    result = service.presign(WORKSPACE_ID, filename="b.bin", size_bytes=3)

    (presigned_key, _) = storage.presigned[0]
    material_id = result["material"]["id"]
    assert presigned_key == f"{WORKSPACE_ID}/{material_id}/v2g0/m_{material_id}/b.bin"
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


def test_deleted_row_key_is_never_reused(service, storage) -> None:
    """#1279 验收项：删行后同文件同名（同 hash）再上传必得新 key——g 段后的
    material_id 唯一段使 key 跨行不可复用——GC 删除批重开（#1314）后构造性安全。"""
    payload = b"reupload-me"
    digest = _sha256(payload)
    first = service.presign(
        WORKSPACE_ID, filename="same.txt", size_bytes=len(payload), content_hash=digest
    )
    (first_key, _) = storage.presigned[0]
    storage.objects[first_key] = payload
    service.complete(WORKSPACE_ID, first["material"]["id"])
    service.delete(WORKSPACE_ID, first["material"]["id"])

    second = service.presign(
        WORKSPACE_ID, filename="same.txt", size_bytes=len(payload), content_hash=digest
    )

    assert second["material"]["id"] != first["material"]["id"]
    assert second["deduplicated"] is False
    (second_key, _) = storage.presigned[1]
    assert second_key != first_key
    assert second_key == (f"{WORKSPACE_ID}/{digest}/v2g0/m_{second['material']['id']}/same.txt")


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


def test_presign_retry_rebuilds_invalid_legacy_key(service, storage, job_db) -> None:
    """#1267 codex P2：收紧前入库的行可能带着非法 key（以 / 结尾等）；重试
    迁到代次段 key 后按当前（已校验）文件名重建，非法旧 key 不再签发。"""
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
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/v2g1/m_mat-legacy/ok.txt"
    assert _storage_key_of(job_db, "mat-legacy") == presigned_key


def test_presign_retry_moves_to_current_generation_key(service, storage, job_db) -> None:
    """#1267 codex R4 P1：重试不复用旧 key——即使旧 key 合法，也迁到
    g{代次} 段的新 key，此前签发的 PUT URL 全部指向死 key。"""
    digest = _sha256(b"legacy-ok")
    legacy_key = f"{WORKSPACE_ID}/{digest}/old.txt"
    _insert_material_row(
        job_db,
        material_id="mat-legacy-ok",
        content_hash=digest,
        filename="old.txt",
        storage_key=legacy_key,
    )

    retry = service.presign(WORKSPACE_ID, filename="old.txt", size_bytes=1, content_hash=digest)

    assert retry["material"]["id"] == "mat-legacy-ok"
    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == f"{WORKSPACE_ID}/{digest}/v2g1/m_mat-legacy-ok/old.txt"
    assert _storage_key_of(job_db, "mat-legacy-ok") == presigned_key
    assert retry["material"]["filename"] == "old.txt"


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
    # R4：每代 key 独立——两次重试签发的 URL 指向各自的 g{代次} 段
    # （presigned[0] 是首发的第 0 代；#1279 起 g 段后带 material_id 唯一段）。
    assert storage.presigned[0][0] == f"{WORKSPACE_ID}/{digest}/v2g0/m_{material_id}/g.txt"
    assert storage.presigned[1][0] == f"{WORKSPACE_ID}/{digest}/v2g1/m_{material_id}/g.txt"
    assert storage.presigned[2][0] == f"{WORKSPACE_ID}/{digest}/v2g2/m_{material_id}/g.txt"


def test_concurrent_retries_derive_key_from_committed_generation(service, storage, job_db) -> None:
    """#1267 codex R5 P1-a：并发双重试——B 的 UPDATE 在行锁后推进代次，
    key 必须由锁内实际新值派生（禁止从事务外快照值造 key）。确定性注入：
    holder 持行 FOR UPDATE 锁，B 阻塞期间 holder 把代次推到 5 再提交。"""
    digest = _sha256(b"race")
    result = service.presign(WORKSPACE_ID, filename="r.txt", size_bytes=1, content_hash=digest)
    material_id = result["material"]["id"]

    outcome: list[str] = []
    entered = threading.Event()

    def _retry() -> None:
        entered.set()
        try:
            service.presign(WORKSPACE_ID, filename="r.txt", size_bytes=1, content_hash=digest)
            outcome.append("ok")
        except Exception as exc:  # 线程内意外失败也要带回主线程定位
            outcome.append(f"error:{exc!r}")

    holder = connect_database(job_db.dsn_identity)
    try:
        with holder:
            # holder 持行锁：B 的 SELECT 拿到 gen 0 快照，UPDATE 阻塞在锁上。
            holder.execute("select id from materials where id=%s for update", (material_id,))
            thread = threading.Thread(target=_retry)
            thread.start()
            assert entered.wait(timeout=5)
            wait_until_blocked_by(backend_pid(holder), thread=thread)
            assert thread.is_alive()
            holder.execute("update materials set upload_generation=5 where id=%s", (material_id,))
        # holder 提交，B 的 UPDATE 以最新行版本（gen 5）求值 → 6
        thread.join(timeout=15)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert outcome == ["ok"]
    assert _generation_of(job_db, material_id) == 6
    (key, _) = storage.presigned[-1]
    assert key == f"{WORKSPACE_ID}/{digest}/v2g6/m_{material_id}/r.txt"
    assert _storage_key_of(job_db, material_id) == key


def test_presign_retry_racing_delete_returns_not_found(service, storage, job_db) -> None:
    """#1269（#1267 复验遗留）：presign 的无锁 SELECT 与重试 UPDATE 之间行被
    并发 delete/TTL 回收——对方持行锁删行并提交后，本事务的 UPDATE 命中 0 行、
    RETURNING 为空。此时必须抛 NotFoundError（404 语义、可安全重试 presign），
    不能 assert 崩成 500（优化模式下断言被剥，退化为 None 解引用）。"""
    digest = _sha256(b"race-delete")
    result = service.presign(WORKSPACE_ID, filename="d.txt", size_bytes=1, content_hash=digest)
    material_id = result["material"]["id"]

    outcome: list[str] = []
    entered = threading.Event()

    def _retry() -> None:
        entered.set()
        try:
            service.presign(WORKSPACE_ID, filename="d.txt", size_bytes=1, content_hash=digest)
            outcome.append("ok")
        except NotFoundError:
            outcome.append("not-found")
        except Exception as exc:  # 线程内意外失败也要带回主线程定位
            outcome.append(f"error:{exc!r}")

    holder = connect_database(job_db.dsn_identity)
    try:
        with holder:
            # holder 持行锁：重试线程的无锁 SELECT 读到旧快照后，UPDATE 阻塞在锁上。
            holder.execute("select id from materials where id=%s for update", (material_id,))
            thread = threading.Thread(target=_retry)
            thread.start()
            assert entered.wait(timeout=5)
            wait_until_blocked_by(backend_pid(holder), thread=thread)
            # 并发 delete 的终局：行被删除并提交，重试的 UPDATE 解锁后命中 0 行。
            holder.execute("delete from materials where id=%s", (material_id,))
        thread.join(timeout=15)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert outcome == ["not-found"]


def test_generation_namespace_unforgeable_by_filename(service, storage) -> None:
    """#1267 codex R5 P1-b：代次段统一存在（首发也是 g0），用户 filename 里
    的 g1/ 无法伪造新代次 key——`g1/foo` 的首发与同 hash `foo` 的重试 key
    必须不同。"""
    digest = _sha256(b"forge")
    # 攻击者先以 g1/foo 首发建行（第 0 代）。
    first = service.presign(WORKSPACE_ID, filename="g1/foo", size_bytes=1, content_hash=digest)
    # 受害者随后以 foo 重试同一 hash 的行 → 代次 1（同一行，content_hash dedup）。
    service.presign(WORKSPACE_ID, filename="foo", size_bytes=1, content_hash=digest)

    material_id = first["material"]["id"]
    (first_key, _) = storage.presigned[0]
    (retry_key, _) = storage.presigned[1]
    assert first_key == f"{WORKSPACE_ID}/{digest}/v2g0/m_{material_id}/g1/foo"
    assert retry_key == f"{WORKSPACE_ID}/{digest}/v2g1/m_{material_id}/foo"
    assert first_key != retry_key


def test_presign_accepts_nested_relative_path(service, storage) -> None:
    """中间带 / 的相对路径合法：材料 key 本就是多段路径，只禁目录形态。"""
    digest = _sha256(b"nested")
    result = service.presign(
        WORKSPACE_ID, filename="dir/sub/file.txt", size_bytes=1, content_hash=digest
    )

    (presigned_key, _) = storage.presigned[0]
    assert presigned_key == (
        f"{WORKSPACE_ID}/{digest}/v2g0/m_{result['material']['id']}/dir/sub/file.txt"
    )
    assert result["material"]["filename"] == "dir/sub/file.txt"


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
    material_id = result["material"]["id"]
    storage_key = f"{WORKSPACE_ID}/{material_id}/v2g0/m_{material_id}/j.txt"
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
    storage_key = f"{WORKSPACE_ID}/{material_id}/v2g0/m_{material_id}/k.txt"
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
    storage_key = f"{WORKSPACE_ID}/{material_id}/v2g0/m_{material_id}/m.txt"
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
    storage.objects[f"{WORKSPACE_ID}/{material_id}/v2g0/m_{material_id}/n.txt"] = b"ok"

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
