"""Materials TTL 治理（design §10，#160）：expires_at 写入、到期翻
expired、expired 新引用被拒、零引用物理删除。

FakeStorage 内存实现注入（同 tests/services/test_materials.py）；instance
设置经 InstanceSettingsStore 直接写文档（校验层由
tests/routes/test_instance_settings_routes.py 覆盖）。
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from typing import BinaryIO

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.material_cache import material_claim_block
from server.app.services.material_ttl import (
    DELETE_GRACE_SECONDS,
    collect_expired_materials,
    expire_due_materials,
    materials_ttl_days,
)
from server.app.services.material_ttl_sweeper import MaterialTtlSweeperThread
from server.app.services.materials import MaterialsService
from server.app.storage import ObjectHead
from shared.material_cache import MaterializeError
from tests.postgres_support import TEST_DATABASE_URL

WORKSPACE_ID = "ws-ttl"
PAYLOAD = b"ttl-material-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_deletes = False

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/download/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        self.objects[storage_key] = data

    def put_stream(self, storage_key: str, stream: BinaryIO, size_bytes: int) -> None:
        self.objects[storage_key] = stream.read()

    def delete_object(self, storage_key: str) -> None:
        if self.fail_deletes:
            raise RuntimeError("s3 delete failed")
        self.objects.pop(storage_key, None)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def service(storage: FakeStorage) -> MaterialsService:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'TTL', 'demo_workflow') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    return MaterialsService(TEST_DATABASE_URL, storage)


def _ready_material(service: MaterialsService, storage: FakeStorage, filename: str) -> str:
    """Run the full presign → PUT → complete flow; returns the material id."""
    payload = PAYLOAD + filename.encode()
    content_hash = hashlib.sha256(payload).hexdigest()
    result = service.presign(
        WORKSPACE_ID,
        filename=filename,
        size_bytes=len(payload),
        content_hash=content_hash,
        created_by="user-1",
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{content_hash}/{filename}"] = payload
    service.complete(WORKSPACE_ID, material_id)
    return material_id


def _material_row(material_id: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select * from materials where id=%s", (material_id,)).fetchone()
    assert row is not None
    return dict(row)


def _set_expires_at(material_id: str, sql_interval: str) -> None:
    """expires_at = now() + <interval>（负值即过去）；'-1 hour' 之类。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update materials set expires_at = now() + %s::interval where id=%s",
            (sql_interval, material_id),
        )


def _reference_job(material_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir, input_json)"
            " values ('job-ref-1', %s, 'wf', 'material', %s, 't', 'pending', 'd', %s)",
            (
                WORKSPACE_ID,
                material_id,
                json.dumps({"type": "material", "material_id": material_id}),
            ),
        )


def test_complete_writes_expires_at_when_ttl_enabled(
    service: MaterialsService, storage: FakeStorage
) -> None:
    InstanceSettingsStore(TEST_DATABASE_URL).put({"materials_ttl_days": 7})

    material_id = _ready_material(service, storage, "a.txt")

    row = _material_row(material_id)
    assert row["status"] == "ready"
    assert row["expires_at"] is not None


def test_complete_leaves_expires_at_null_when_ttl_disabled(
    service: MaterialsService, storage: FakeStorage
) -> None:
    material_id = _ready_material(service, storage, "b.txt")

    row = _material_row(material_id)
    assert row["status"] == "ready"
    assert row["expires_at"] is None


def test_materials_ttl_days_defensive_reads(service: MaterialsService) -> None:
    store = InstanceSettingsStore(TEST_DATABASE_URL)
    assert materials_ttl_days(TEST_DATABASE_URL) == 0  # 未设置
    store.put({"materials_ttl_days": 30})
    assert materials_ttl_days(TEST_DATABASE_URL) == 30
    for bad in (-1, "30", 1.5, True, None):
        store.put({"materials_ttl_days": bad})
        assert materials_ttl_days(TEST_DATABASE_URL) == 0


def test_materials_ttl_days_passes_facade_through_untouched(
    service: MaterialsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#247: a JobQueries facade must reach InstanceSettingsStore as the
    facade itself, never unwrapped to a bare DSN — the original bug (drawing
    the DSN back out via dsn_identity) was invisible to every existing test
    because they all pass DSN strings."""

    class FakeFacade:
        """Stands in for JobQueries: any non-str ConnectSource shape."""

        dsn_identity = TEST_DATABASE_URL

    facade = FakeFacade()
    received: list[object] = []
    real_init = InstanceSettingsStore.__init__

    def spy_init(self: InstanceSettingsStore, database_dsn: object) -> None:
        received.append(database_dsn)
        real_init(self, database_dsn)  # type: ignore[arg-type]

    monkeypatch.setattr(InstanceSettingsStore, "__init__", spy_init)
    materials_ttl_days(facade)  # type: ignore[arg-type]
    assert received and received[0] is facade


def test_expire_due_materials_flips_only_past_due(
    service: MaterialsService, storage: FakeStorage
) -> None:
    due = _ready_material(service, storage, "due.txt")
    future = _ready_material(service, storage, "future.txt")
    _set_expires_at(due, "-1 hour")
    _set_expires_at(future, "1 hour")

    assert expire_due_materials(TEST_DATABASE_URL) == 1

    assert _material_row(due)["status"] == "expired"
    assert _material_row(future)["status"] == "ready"


def test_expired_material_rejected_at_claim_resolution(
    service: MaterialsService, storage: FakeStorage
) -> None:
    """新引用被拒：claim 解析链只接受 ready（material_cache._ready_row）。"""
    material_id = _ready_material(service, storage, "expired.txt")
    _set_expires_at(material_id, "-1 hour")
    assert expire_due_materials(TEST_DATABASE_URL) == 1
    job = {"input_json": {"type": "material", "material_id": material_id}}

    with pytest.raises(MaterializeError, match="not ready"):
        material_claim_block(TEST_DATABASE_URL, WORKSPACE_ID, job, storage=storage)


def test_expired_material_revived_by_same_hash_presign(
    service: MaterialsService, storage: FakeStorage
) -> None:
    """复活链路：ready → expired → 同 hash presign 重置 uploading →
    complete 重新打 expires_at（materials.py presign 的 stale-row 分支）。"""
    InstanceSettingsStore(TEST_DATABASE_URL).put({"materials_ttl_days": 7})
    material_id = _ready_material(service, storage, "revive.txt")
    _set_expires_at(material_id, "-1 hour")
    assert expire_due_materials(TEST_DATABASE_URL) == 1
    assert _material_row(material_id)["status"] == "expired"

    payload = PAYLOAD + b"revive.txt"
    content_hash = hashlib.sha256(payload).hexdigest()
    result = service.presign(
        WORKSPACE_ID,
        filename="revive.txt",
        size_bytes=len(payload),
        content_hash=content_hash,
        created_by="user-1",
    )
    assert result["material"]["id"] == material_id
    assert result["deduplicated"] is False
    assert result["upload_url"] is not None
    assert _material_row(material_id)["status"] == "uploading"

    service.complete(WORKSPACE_ID, material_id)

    row = _material_row(material_id)
    assert row["status"] == "ready"
    assert row["expires_at"] is not None
    expires_at = datetime.fromisoformat(str(row["expires_at"]))
    assert expires_at > datetime.now(UTC)


def test_collect_deletes_unreferenced_past_grace(
    service: MaterialsService, storage: FakeStorage
) -> None:
    material_id = _ready_material(service, storage, "gone.txt")
    _set_expires_at(material_id, f"-{DELETE_GRACE_SECONDS + 60} seconds")
    expire_due_materials(TEST_DATABASE_URL)
    storage_key = str(_material_row(material_id)["storage_key"])
    assert storage_key in storage.objects

    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 1

    assert storage.objects == {}
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 from materials where id=%s", (material_id,)).fetchone()
    assert row is None


def test_collect_keeps_referenced_material(service: MaterialsService, storage: FakeStorage) -> None:
    material_id = _ready_material(service, storage, "referenced.txt")
    _set_expires_at(material_id, f"-{DELETE_GRACE_SECONDS + 60} seconds")
    expire_due_materials(TEST_DATABASE_URL)
    _reference_job(material_id)

    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 0

    row = _material_row(material_id)
    assert row["status"] == "expired"  # 行保留（引用中的 job 不强行失效）
    assert str(row["storage_key"]) in storage.objects


def test_collect_keeps_bundle_member(service: MaterialsService, storage: FakeStorage) -> None:
    """bundle 成员算引用（#156）：对象先删、行删除被外键回滚的方向不可接受。"""
    material_id = _ready_material(service, storage, "member.txt")
    bundle = MaterialBundlesService(TEST_DATABASE_URL).create(
        WORKSPACE_ID,
        name="folder",
        members=[{"material_id": material_id, "path": "member.txt"}],
        created_by="user-1",
    )
    _set_expires_at(material_id, f"-{DELETE_GRACE_SECONDS + 60} seconds")
    expire_due_materials(TEST_DATABASE_URL)

    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 0

    row = _material_row(material_id)
    assert row["status"] == "expired"  # 行与对象都保留，bundle 仍可解析成员
    assert str(row["storage_key"]) in storage.objects
    # bundle 删除后守卫解除，下一轮正常回收。
    MaterialBundlesService(TEST_DATABASE_URL).delete(WORKSPACE_ID, bundle["id"])
    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 1
    assert storage.objects == {}


def test_collect_keeps_material_within_grace(
    service: MaterialsService, storage: FakeStorage
) -> None:
    material_id = _ready_material(service, storage, "grace.txt")
    _set_expires_at(material_id, "-1 seconds")  # 已到期但仍在 grace 窗口内
    expire_due_materials(TEST_DATABASE_URL)

    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 0
    assert _material_row(material_id)["status"] == "expired"


def test_collect_keeps_row_when_object_delete_fails(
    service: MaterialsService, storage: FakeStorage
) -> None:
    """行先删、对象后删（事务外）：对象删除失败留下孤儿对象交给 bucket
    lifecycle 兜底，行已删除（不再原地重试同一行）。"""
    material_id = _ready_material(service, storage, "retry.txt")
    _set_expires_at(material_id, f"-{DELETE_GRACE_SECONDS + 60} seconds")
    expire_due_materials(TEST_DATABASE_URL)
    storage_key = str(_material_row(material_id)["storage_key"])
    storage.fail_deletes = True

    # collect 仍成功（行删除与对象删除解耦），对象删除异常被逐条吞掉。
    assert collect_expired_materials(TEST_DATABASE_URL, storage) == 1
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select 1 from materials where id=%s", (material_id,)).fetchone()
    assert row is None
    # 对象删除失败 → 孤儿对象留存（bucket lifecycle rule 兜底，不重试行）。
    assert storage_key in storage.objects


def test_thread_run_once_noop_without_storage(service: MaterialsService) -> None:
    thread = MaterialTtlSweeperThread(TEST_DATABASE_URL, None)
    thread.run_once()  # 不报错、不触碰 DB 之外的任何状态
