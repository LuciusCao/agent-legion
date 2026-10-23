"""材料对象写入协议 × S3 孤儿 GC 材料删除批的交错决策表（#1314）。

不变式：任何被 GC 删除的材料前缀对象，在删除时刻及之后都不可能被任何
materials 行（含 uploading intent）引用。

每个用例都在真实 Postgres + 内存对象存储上，用 CLI 同一编排
（``s3_material_gc.collect_gc_report`` + ``s3_jobs_gc.apply_gc`` +
``s3_material_gc.apply_material_gc``，``--grace-hours 0``）把 GC ``--apply``
插进写者的某一步，再核对不变式。
对象 LastModified 由 ``put_age_hours`` 注入：故障注入用例把它设成 48h 前，
模拟「PUT 后卡住远超任何年龄下限」的写者——年龄门帮不上忙，只剩写端
协议（intent 行先于对象）能保护对象。

``test_harness_detects_unprotected_writer`` 是 mutation 证据：摘掉 intent
（stage 置空）后同一场景对象被 GC 删掉。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest

from server.app.services import s3_jobs_gc, s3_material_gc
from server.app.services.job_errors import ConflictError
from server.app.services.material_object_publish import (
    MaterialObjectWrite,
    publish_material_objects,
)
from server.app.services.materials import MaterialsService
from server.app.services.run_text_batch import store_text_batch
from server.app.services.s3_jobs_gc import ObjectEntry
from tests.fakes.storage import FakeObjectStorage


class ClockedStorage(FakeObjectStorage):
    """带 LastModified 与 PUT 钩子的内存存储；兼作 apply_gc 的批删客户端。"""

    def __init__(self) -> None:
        super().__init__()
        self.mtimes: dict[str, datetime] = {}
        self.put_age_hours = 0.0
        self.after_put: Callable[[str], None] | None = None

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        super().put_object(storage_key, data, content_type)
        self.mtimes[storage_key] = datetime.now(UTC) - timedelta(hours=self.put_age_hours)
        if self.after_put is not None:
            hook, self.after_put = self.after_put, None
            hook(storage_key)

    def delete_object(self, storage_key: str) -> None:
        super().delete_object(storage_key)
        self.mtimes.pop(storage_key, None)

    def delete_objects(self, Bucket: str, Delete: dict) -> dict:  # noqa: N803 - boto3 shape
        for obj in Delete["Objects"]:
            self.delete_object(obj["Key"])
        return {"Errors": []}

    def entries(self, prefix: str) -> Iterator[ObjectEntry]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ObjectEntry(key, self.mtimes[key], len(self.objects[key]))


def run_gc_apply(job_db, storage: ClockedStorage) -> s3_material_gc.MaterialApplyResult:
    """CLI ``--apply --grace-hours 0 --staging-grace-hours 0`` 的同一编排；
    返回材料道结果。"""
    key_exists = s3_jobs_gc.make_db_key_existence(job_db)
    report, split = s3_material_gc.collect_gc_report(
        storage.entries, key_exists, grace_hours=0.0, staging_grace_hours=0.0
    )
    revalidate = job_db.existing_object_storage_keys
    s3_jobs_gc.apply_gc(storage, "bkt", report, revalidate=revalidate)
    return s3_material_gc.apply_material_gc(storage, "bkt", split.deletable, revalidate)


def _rows(job_db, workspace_id: str) -> list[dict]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, status, content_hash, storage_key from materials where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _assert_invariant(job_db, storage: ClockedStorage, deleted: list[str]) -> None:
    """被 GC 删除的 key 此刻没有任何行引用；有行引用的 key 对象都在
    （uploading 行的 key 可尚未 PUT，除外）。"""
    referenced = job_db.existing_object_storage_keys(deleted) if deleted else set()
    assert referenced == set(), f"GC deleted keys still referenced: {referenced}"
    with job_db.read() as conn:
        ready = conn.execute("select storage_key from materials where status='ready'").fetchall()
    for row in ready:
        assert row["storage_key"] in storage.objects, f"ready row lost its object: {row}"


@pytest.fixture
def storage() -> ClockedStorage:
    return ClockedStorage()


@pytest.fixture
def workspace_id(job_db) -> str:
    return str(job_db.create_workspace("gc-protocol")["id"])


def _inline(job_db, storage, workspace_id: str, text: bytes = b"A") -> dict[int, str]:
    return store_text_batch(job_db, storage, workspace_id, [(0, "a.md", text)], "tester")


def _demo(job_db, storage, workspace_id: str, text: bytes = b"A") -> dict[str, str | None]:
    write = MaterialObjectWrite(
        content_hash=hashlib.sha256(text).hexdigest(),
        filename="a.md",
        content_type="text/markdown; charset=utf-8",
        payload=text,
        key_tail="a.md",
    )
    return publish_material_objects(
        job_db,
        storage,
        workspace_id,
        [write],
        created_by="system",
        ttl_days=0,
        on_non_ready="skip",
    )


# ---- 服务端写者（inline / demo seed chokepoint）× GC 插入点 ---------------


@pytest.mark.parametrize("writer", [_inline, _demo], ids=["inline", "demo_seed"])
def test_gc_apply_between_put_and_publish_keeps_object(job_db, storage, workspace_id, writer):
    """故障注入（#1314 验收）：PUT 之后、发布之前跑 GC --apply（写者已卡住
    48h，任何年龄门都已失效）→ intent 行让新鲜反查救下对象；随后发布成功，
    ready 行指向存在的对象。"""
    storage.put_age_hours = 48.0
    gc_results: list[s3_material_gc.MaterialApplyResult] = []
    deleted_before: dict[str, bytes] = {}

    def gc_mid_write(key: str) -> None:
        deleted_before.update(storage.objects)
        gc_results.append(run_gc_apply(job_db, storage))
        assert key in storage.objects, "GC deleted an object whose intent row exists"

    storage.after_put = gc_mid_write
    writer(job_db, storage, workspace_id)

    (result,) = gc_results
    assert result.deleted == 0
    assert result.skipped_revalidated == 0  # 扫描期 key_exists 就已见 intent 行
    (row,) = _rows(job_db, workspace_id)
    assert row["status"] == "ready"
    assert storage.objects[row["storage_key"]] == b"A"
    _assert_invariant(job_db, storage, [k for k in deleted_before if k not in storage.objects])


def test_harness_detects_unprotected_writer(job_db, storage, workspace_id, monkeypatch):
    """mutation 证据：摘掉 intent（stage 置空 = 回到「先 PUT 后建行」）后，
    同一故障注入场景里 GC 删掉了在途对象——上一条用例的绿不是空转。写者
    随即失败（PUT 后的 HEAD 读不到对象；即便 GC 落在 HEAD 之后，发布侧的
    CAS——intent 行必须仍在——也会拒绝），绝不留下指向已删对象的 ready 行。"""
    monkeypatch.setattr(
        "server.app.services.material_object_publish.stage_material_intents",
        lambda *args, **kwargs: None,
    )
    storage.put_age_hours = 48.0
    observed: list[bool] = []

    def gc_mid_write(key: str) -> None:
        run_gc_apply(job_db, storage)
        observed.append(key in storage.objects)

    storage.after_put = gc_mid_write
    with pytest.raises((ConflictError, OSError)):
        _demo(job_db, storage, workspace_id)

    assert observed == [False]
    assert _rows(job_db, workspace_id) == []


@pytest.mark.parametrize("writer", [_inline, _demo], ids=["inline", "demo_seed"])
def test_intent_row_deleted_before_publish_never_publishes(
    job_db, storage, workspace_id, writer, monkeypatch
):
    """intent 行在 HEAD 之后、发布之前被删（用户在材料列表删了这条
    uploading 行——删除同时删对象；或任何让该行消失的路径）：发布侧 CAS
    拒绝（ConflictError），绝不落一条指向可能已被删对象的 ready 行；对象
    此后无行引用，GC 回收。"""
    from server.app.jobs.queries.material_intent import publish_material_intents

    service = MaterialsService(job_db, storage)

    def delete_intent_then_publish(source, ws, intents, **kwargs):
        for intent in intents:
            service.delete(ws, intent["material_id"])
        return publish_material_intents(source, ws, intents, **kwargs)

    monkeypatch.setattr(
        "server.app.services.material_object_publish.publish_material_intents",
        delete_intent_then_publish,
    )
    with pytest.raises(ConflictError, match="staging row"):
        writer(job_db, storage, workspace_id)

    assert _rows(job_db, workspace_id) == []
    run_gc_apply(job_db, storage)
    _assert_invariant(job_db, storage, [])
    assert storage.objects == {}


@pytest.mark.parametrize("writer", [_inline, _demo], ids=["inline", "demo_seed"])
def test_gc_after_failed_publish_reclaims_compensation_leftover(
    job_db, storage, workspace_id, writer
):
    """补偿路径：HEAD 空 ETag → 写者失败；release 先删 intent 行再删对象。
    对象删除失败时留下的单次使用 key 孤儿由 GC 材料删除批回收，且此后
    没有任何行会引用它。"""
    storage.put_age_hours = 48.0
    storage.no_etag = True
    storage.fail_deletes = True
    with pytest.raises(Exception):  # noqa: B017 - inline 映射 503，demo 原样上抛
        writer(job_db, storage, workspace_id)
    assert _rows(job_db, workspace_id) == []
    (leftover,) = storage.objects
    storage.fail_deletes = False

    result = run_gc_apply(job_db, storage)

    assert result.deleted == 1
    assert storage.objects == {}
    _assert_invariant(job_db, storage, [leftover])


def test_concurrent_identical_text_loser_object_reclaimed(job_db, storage, workspace_id):
    """同内容两次写入：后到者复用 ready 胜者、删除自己的 intent 与对象；GC
    之后胜者对象仍在（不变式两面）。"""
    first = _inline(job_db, storage, workspace_id)
    second = _inline(job_db, storage, workspace_id)
    assert first == second
    run_gc_apply(job_db, storage)
    (row,) = _rows(job_db, workspace_id)
    assert set(storage.objects) == {row["storage_key"]}


# ---- 浏览器写者（presign / 重试 / repair）× GC ---------------------------


def _presign(service: MaterialsService, workspace_id: str, payload: bytes, *, hashed=True):
    return service.presign(
        workspace_id,
        filename="doc.txt",
        size_bytes=len(payload),
        content_hash=hashlib.sha256(payload).hexdigest() if hashed else "",
    )


def _key(job_db, material_id: str) -> str:
    with job_db.read() as conn:
        row = conn.execute("select storage_key from materials where id=%s", (material_id,))
        return str(row.fetchone()["storage_key"])


@pytest.mark.parametrize("hashed", [True, False], ids=["hash", "hashless"])
def test_presign_uploading_row_never_completed_keeps_object(job_db, storage, workspace_id, hashed):
    """签出后永不 complete 的 uploading 行（URL 早已过期）：对象是 intent，
    不是孤儿——行仍引用 key，GC 不删。"""
    service = MaterialsService(job_db, storage)
    upload = _presign(service, workspace_id, b"payload", hashed=hashed)
    key = _key(job_db, upload["material"]["id"])
    storage.put_age_hours = 48.0
    storage.put_object(key, b"payload")

    result = run_gc_apply(job_db, storage)

    assert result.deleted == 0
    assert key in storage.objects


def test_presign_retry_superseded_generation_reclaimed(job_db, storage, workspace_id):
    """presign 重试把行迁到 v2g1：上一代对象成孤儿被回收；当前代对象
    （行引用）保留；代次单调，行永远回不到 v2g0。"""
    service = MaterialsService(job_db, storage)
    first = _presign(service, workspace_id, b"payload")
    old_key = _key(job_db, first["material"]["id"])
    storage.put_age_hours = 48.0
    storage.put_object(old_key, b"payload")
    second = _presign(service, workspace_id, b"payload")
    new_key = _key(job_db, second["material"]["id"])
    assert new_key != old_key and "/v2g1/" in new_key
    storage.put_object(new_key, b"payload")

    run_gc_apply(job_db, storage)

    assert old_key not in storage.objects
    assert new_key in storage.objects
    _assert_invariant(job_db, storage, [old_key])


@pytest.mark.parametrize("late_put_age_hours", [48.0, 2.0])
def test_ghost_put_after_row_deleted_is_reclaimed(
    job_db, storage, workspace_id, late_put_age_hours
):
    """幽灵写端：uploading 行被删除后，持有未过期 URL（presign 1h）的客户端
    迟到 PUT 重建对象。行已删、id 不复用 → 该 key 永不再被引用，GC 删除
    安全；GC 之后再来一次迟到 PUT 也只是新孤儿。"""
    service = MaterialsService(job_db, storage)
    upload = _presign(service, workspace_id, b"payload")
    material_id = upload["material"]["id"]
    key = _key(job_db, material_id)
    service.delete(workspace_id, material_id)
    storage.put_age_hours = late_put_age_hours
    storage.put_object(key, b"payload")

    result = run_gc_apply(job_db, storage)

    assert result.deleted == 1
    _assert_invariant(job_db, storage, [key])


def test_ghost_put_after_failed_flip_stays_protected(job_db, storage, workspace_id):
    """complete 核验失败翻 failed：行保留 key → 对象仍是 intent，GC 不删
    （客户端可再 presign 重试，届时旧代对象才成孤儿）。"""
    service = MaterialsService(job_db, storage)
    upload = _presign(service, workspace_id, b"payload")
    material_id = upload["material"]["id"]
    key = _key(job_db, material_id)
    storage.put_age_hours = 48.0
    storage.put_object(key, b"wrong!!")
    with pytest.raises(Exception):  # noqa: B017 - MaterialVerificationError
        service.complete(workspace_id, material_id)

    run_gc_apply(job_db, storage)

    assert key in storage.objects


def test_gc_concurrent_with_repair_rotation(job_db, storage, workspace_id):
    """GC × repair 轮换：GC 扫描（缓存 key 集合）后 repair 把 ready 行轮换到
    v2g1 并重传——旧 key 在删除前新鲜反查时已无行 → 删除安全（旧对象正是
    被判毒化的那份）；新 key 由轮换后的行引用 → 保留。"""
    service = MaterialsService(job_db, storage)
    upload = _presign(service, workspace_id, b"payload", hashed=False)
    material_id = upload["material"]["id"]
    old_key = _key(job_db, material_id)
    storage.put_age_hours = 48.0
    storage.put_object(old_key, b"payload")
    service.complete(workspace_id, material_id)

    key_exists = s3_jobs_gc.make_db_key_existence(job_db)
    _, split = s3_material_gc.collect_gc_report(
        storage.entries, key_exists, grace_hours=0.0, staging_grace_hours=0.0
    )
    assert split.deletable == []  # 扫描时 old_key 仍被 ready 行引用
    repaired = service.request_material_reupload(workspace_id, material_id)
    new_key = str(repaired["storage_key"])
    storage.put_object(new_key, b"payload")
    # 第二轮扫描：old_key 已无行。
    result = run_gc_apply(job_db, storage)

    assert old_key not in storage.objects
    assert new_key in storage.objects
    assert result.deleted == 1
    _assert_invariant(job_db, storage, [old_key])


# ---- 协议外写者（#1312 期旧 Host：先 PUT 后建行）× 年龄下限 --------------


@pytest.mark.parametrize(
    ("put_age_hours", "deleted"),
    [(0.0, False), (0.5, False), (2.0, True)],
    ids=["fresh", "inside_floor", "stalled_past_floor"],
)
def test_legacy_order_writer_relies_on_age_floor(
    job_db, storage, workspace_id, put_age_hours, deleted
):
    """混合版本纵深（选项 2）：#1312 期 Host 的 inline/demo seed 仍「先 PUT
    后建行」、已发 v2g key。PUT→建行窗口内对象无行：只有材料年龄下限
    （1h，--grace-hours 0 也压不穿）保护它。卡住超过下限的旧写者是声明的
    残余窗口（对象被删，随后旧写者落一条指向缺失对象的 ready 行——消费侧
    缺对象失败，与 #1312 base 的 bucket lifecycle 残余同类）。"""
    key = f"{workspace_id}/{'a' * 64}/v2g0/m_{'b' * 32}/inline-{'c' * 32}"
    storage.put_age_hours = put_age_hours
    storage.put_object(key, b"A")  # 旧写者：对象先落，行还没建

    run_gc_apply(job_db, storage)

    assert (key not in storage.objects) is deleted
