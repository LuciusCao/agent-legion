"""#1313：恢复重绑「扫描 → CAS」之间的并发写者 × 四钉。

``scripts/rebind_material_etags.py`` 是 verify-then-CAS：新 etag 是从**扫描时
的 key** 核验出来的，CAS 必须钉住扫描快照的 (object_etag, storage_key,
upload_generation, status)。每格用真实写路径（MaterialsService 的 repair /
presign 重试 / complete / delete、TTL 过期、另一次 rebind）在 CAS 前一刻插入
写者，断言：

- 写者改动的字段恰为表中声明的那几个（表本身被测试钉住）；
- CAS 落空 → 报告 raced、退出码 1、行保持写者留下的样子（rebind 不覆盖）。

单钉格用直接 SQL 只改一个字段，证明四个钉各自独立生效（摘掉任一钉就转红）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from scripts.rebind_material_etags import print_report, rebind_material_etags
from server.app.services.material_ttl import expire_due_materials
from server.app.services.materials import MaterialsService, MaterialVerificationError
from server.app.storage import ObjectHead
from tests.fakes.storage import FakeObjectStorage, etag_of
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
service = materials_testlib.service
PAYLOAD = b"restored-bytes"
PINS = ("object_etag", "storage_key", "upload_generation", "status")


class _RestoredStorage(FakeObjectStorage):
    """``multipart`` 内的 key = 快照恢复后经 aws s3 cp 分段重传的对象：字节
    不变、ETag 变为分段形态（``<md5>-2``），与行上备份前的单段 ETag 失配。"""

    def __init__(self) -> None:
        super().__init__()
        self.multipart: set[str] = set()

    def _restored_etag(self, storage_key: str) -> str:
        return etag_of(self.objects[storage_key]) + "-2"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        head = super().head_object(storage_key)
        if head is None or storage_key not in self.multipart:
            return head
        return ObjectHead(size_bytes=head.size_bytes, etag=self._restored_etag(storage_key))

    def open_stream(self, storage_key: str, *, if_match: str = ""):
        if storage_key in self.multipart and if_match == self._restored_etag(storage_key):
            if_match = etag_of(self.objects[storage_key])
        return super().open_stream(storage_key, if_match=if_match)


@pytest.fixture
def storage() -> _RestoredStorage:
    return _RestoredStorage()


def _row(job_db, material_id: str) -> dict[str, Any] | None:
    with job_db.connect() as conn:
        row = conn.execute(
            "select object_etag, storage_key, upload_generation, status from materials where id=%s",
            (material_id,),
        ).fetchone()
    return None if row is None else dict(row)


def _presign(service: MaterialsService, payload: bytes, *, hashed: bool) -> dict[str, Any]:
    return service.presign(
        WORKSPACE_ID,
        filename="notes.txt",
        size_bytes=len(payload),
        content_type="text/plain",
        content_hash=materials_testlib.sha256_hex(payload) if hashed else "",
        created_by="user-1",
    )


def _ready_restored(service, storage, job_db, *, hashed: bool) -> str:
    """complete 落 ready（单段 ETag 入行），随后快照恢复把对象换成分段形态。"""
    material_id = _presign(service, PAYLOAD, hashed=hashed)["material"]["id"]
    key = materials_testlib.storage_key_of(job_db, material_id)
    storage.objects[key] = PAYLOAD
    service.complete(WORKSPACE_ID, material_id)
    storage.multipart.add(key)
    return material_id


def _uploading_with_stale_etag(service, storage, job_db, *, hashed: bool) -> str:
    """ready → expired → presign 重试：行回 uploading、key/代次轮换，但 presign
    重试**不清 object_etag**——扫描面会带出这条「旧 etag + 新 key」的行；新 key
    上已有（分段形态的）字节，rebind 核验通过后走到 CAS。presign 重试只对
    hash 行存在（无 hash 的 presign 恒建新行）。"""
    assert hashed
    material_id = _ready_restored(service, storage, job_db, hashed=hashed)
    with job_db.connect() as conn:
        conn.execute("update materials set status='expired' where id=%s", (material_id,))
    _presign(service, PAYLOAD, hashed=True)
    key = materials_testlib.storage_key_of(job_db, material_id)
    storage.objects[key] = PAYLOAD
    storage.multipart.add(key)
    return material_id


def _w_repair(service, storage, job_db, material_id: str) -> None:
    service.request_material_reupload(WORKSPACE_ID, material_id)


def _w_repair_then_same_bytes(service, storage, job_db, material_id: str) -> None:
    """#1313 原始场景：repair 轮换后客户端单段重传同字节 → complete 落回与扫描
    时**相同**的 etag（ABA），只有 key/代次钉能拦。"""
    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    storage.objects[outcome["storage_key"]] = PAYLOAD
    service.complete(
        WORKSPACE_ID, material_id, expected_upload_generation=outcome["upload_generation"]
    )


def _w_presign_retry(service, storage, job_db, material_id: str) -> None:
    _presign(service, PAYLOAD, hashed=True)


def _w_complete_ok(service, storage, job_db, material_id: str) -> None:
    service.complete(WORKSPACE_ID, material_id)


def _w_complete_failed(service, storage, job_db, material_id: str) -> None:
    key = materials_testlib.storage_key_of(job_db, material_id)
    storage.objects[key] = b"poisoned-bytes"  # 同长异内容 → sha256 不符
    with pytest.raises(MaterialVerificationError):
        service.complete(WORKSPACE_ID, material_id)


def _w_ttl_expire(service, storage, job_db, material_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update materials set expires_at = now() - interval '1 second' where id=%s",
            (material_id,),
        )
    assert expire_due_materials(job_db.dsn_identity) == 1


def _w_delete(service, storage, job_db, material_id: str) -> None:
    service.delete(WORKSPACE_ID, material_id)


def _w_concurrent_rebind(service, storage, job_db, material_id: str) -> None:
    assert rebind_material_etags(storage, job_db, apply=True).rebound == [material_id]


Setup = Callable[..., str]
Writer = Callable[..., None]

# 写者 × 字段表（与 PR #1313 body 同表）：changed = 写者改动的钉；None = 行被删。
# 每格的 CAS 拦截依据 = changed 中任一钉（扫描快照与现行值不再相等）。
RACES: list[tuple[str, Setup, bool, Writer, set[str] | None]] = [
    ("repair", _ready_restored, False, _w_repair, set(PINS)),
    (
        "repair+same-bytes-complete(etag ABA)",
        _ready_restored,
        False,
        _w_repair_then_same_bytes,
        {"storage_key", "upload_generation"},
    ),
    (
        "presign-retry(keeps etag)",
        _uploading_with_stale_etag,
        True,
        _w_presign_retry,
        {"storage_key", "upload_generation"},
    ),
    (
        "complete-ok",
        _uploading_with_stale_etag,
        True,
        _w_complete_ok,
        {"object_etag", "status"},
    ),
    ("complete-failed", _uploading_with_stale_etag, True, _w_complete_failed, {"status"}),
    ("ttl-expire", _ready_restored, True, _w_ttl_expire, {"status"}),
    ("delete", _ready_restored, False, _w_delete, None),
    ("concurrent-rebind", _ready_restored, False, _w_concurrent_rebind, {"object_etag"}),
]


class _RacingQueries:
    """真实 JobQueries + 在 CAS 前一刻插入一次写者（模拟扫描后的并发写）。"""

    def __init__(self, job_db, writer: Callable[[], None]) -> None:
        self.job_db = job_db
        self.writer: Callable[[], None] | None = writer

    def materials_with_object_etag(self) -> list[dict[str, Any]]:
        return self.job_db.materials_with_object_etag()

    def rebind_material_etag(self, material_id: str, **cas: Any) -> bool:
        if self.writer is not None:
            writer, self.writer = self.writer, None
            writer()
        return self.job_db.rebind_material_etag(material_id, **cas)


def _changed_pins(before: dict[str, Any], after: dict[str, Any] | None) -> set[str] | None:
    return None if after is None else {pin for pin in PINS if before[pin] != after[pin]}


@pytest.mark.parametrize(
    ("setup", "hashed", "writer", "changed"),
    [pytest.param(*case[1:], id=case[0]) for case in RACES],
)
def test_concurrent_writer_between_scan_and_cas_is_reported_as_race(
    service, storage, job_db, setup: Setup, hashed: bool, writer: Writer, changed
) -> None:
    material_id = setup(service, storage, job_db, hashed=hashed)
    scanned = _row(job_db, material_id)
    assert scanned is not None
    after_writer: list[dict[str, Any] | None] = []

    def race() -> None:
        writer(service, storage, job_db, material_id)
        after_writer.append(_row(job_db, material_id))

    report = rebind_material_etags(storage, _RacingQueries(job_db, race), apply=True)

    assert _changed_pins(scanned, after_writer[0]) == changed
    assert report.rebound == [] and report.unresolved == []
    assert len(report.raced) == 1 and report.raced[0].startswith(f"{material_id}: ")
    assert print_report(report, apply=True) == 1
    # CAS 落空即不写：行保持写者留下的样子。
    assert _row(job_db, material_id) == after_writer[0]


def test_no_concurrent_writer_rebinds_to_restored_etag(service, storage, job_db) -> None:
    """对照格：无并发写者 → 四钉全中，重绑为恢复后对象的 ETag，退出码 0。"""
    material_id = _ready_restored(service, storage, job_db, hashed=False)
    key = materials_testlib.storage_key_of(job_db, material_id)
    report = rebind_material_etags(storage, job_db, apply=True)
    assert report.rebound == [material_id] and not report.failed
    assert print_report(report, apply=True) == 0
    assert materials_testlib.object_etag_of(job_db, material_id) == storage.head_object(key).etag


_SINGLE_PIN_MUTATIONS = {
    "object_etag": "update materials set object_etag='etag-other' where id=%s",
    "storage_key": "update materials set storage_key=storage_key || '.moved' where id=%s",
    "upload_generation": "update materials set upload_generation=upload_generation + 7 where id=%s",
    "status": "update materials set status='failed' where id=%s",
}


@pytest.mark.parametrize("pin", PINS)
def test_each_pin_alone_rejects_the_cas(service, storage, job_db, pin: str) -> None:
    """单钉 mutation 格：只改一个字段（其余三钉不变）CAS 也必须落空——任何
    一个钉从 WHERE 里摘掉，对应参数就转红。代次钉没有真实写者能单独改动
    （现有写者轮换代次必同时轮换 key），这里是它唯一的独立证据。"""
    material_id = _ready_restored(service, storage, job_db, hashed=True)
    scanned = _row(job_db, material_id)
    assert scanned is not None
    after: list[dict[str, Any] | None] = []

    def mutate() -> None:
        with job_db.connect() as conn:
            conn.execute(_SINGLE_PIN_MUTATIONS[pin], (material_id,))
        after.append(_row(job_db, material_id))

    report = rebind_material_etags(storage, _RacingQueries(job_db, mutate), apply=True)
    assert _changed_pins(scanned, after[0]) == {pin}
    assert report.rebound == [] and len(report.raced) == 1
    assert _row(job_db, material_id) == after[0]
