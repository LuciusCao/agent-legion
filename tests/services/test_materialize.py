"""Host 物化服务：material_runtime_block / material_claim_block（design §6.2）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.app.services.material_cache import (
    is_material_input,
    material_claim_block,
    material_runtime_block,
)
from shared.material_cache import MaterializeError
from tests.fakes.storage import FakeObjectStorage

WORKSPACE_ID = "ws-mat-cache"
PAYLOAD = b"host-side-material" * 50
HASH = hashlib.sha256(PAYLOAD).hexdigest()
MATERIAL_ID = "mat-1"
STORAGE_KEY = f"{WORKSPACE_ID}/{HASH}/notes.txt"


def _fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage(objects={STORAGE_KEY: PAYLOAD})


FakeStorage = _fake_storage


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def material(job_db):
    """一个 ready 的 material 行 + 所属 workspace。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'MatCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials("
            " id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by"
            ") values (%s, %s, %s, 'notes.txt', 'text/plain', %s, %s, 'ready', 'user-1')",
            (MATERIAL_ID, WORKSPACE_ID, HASH, len(PAYLOAD), STORAGE_KEY),
        )
    return MATERIAL_ID


def _job(input_doc: dict | None) -> dict:
    return {"id": "job-1", "input_json": json.dumps(input_doc) if input_doc else ""}


def test_is_material_input() -> None:
    assert is_material_input(_job({"type": "material", "material_id": "m"}))
    assert not is_material_input(_job({"type": "ref", "external_id": "q-1"}))
    assert not is_material_input(_job(None))
    assert not is_material_input({"input_json": "not-json"})


def test_runtime_block_materializes_and_hits_cache(
    job_db, material, storage, tmp_path: Path
) -> None:
    job = _job({"type": "material", "material_id": material})

    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )

    assert block is not None
    assert block["material_id"] == material
    assert block["filename"] == "notes.txt"
    assert block["content_type"] == "text/plain"
    assert block["content_hash"] == HASH
    path = Path(block["path"])
    assert path == tmp_path / HASH[:2] / HASH
    assert path.read_bytes() == PAYLOAD
    assert storage.opened == 1

    again = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert again is not None and again["path"] == str(path)
    assert storage.opened == 1, "cache hit must not re-download"


def test_runtime_block_returns_none_for_non_material(job_db, storage, tmp_path: Path) -> None:
    assert (
        material_runtime_block(
            job_db.dsn_identity,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "ref", "external_id": "q-1"}),
            storage=storage,
        )
        is None
    )
    assert material_runtime_block(job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(None)) is None


def test_runtime_block_errors_are_readable(job_db, material, storage, tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="missing material_id"):
        material_runtime_block(
            job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job({"type": "material"}), storage=storage
        )
    with pytest.raises(MaterializeError, match="material not found"):
        material_runtime_block(
            job_db.dsn_identity,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": "nope"}),
            storage=storage,
        )
    with job_db.connect() as conn:
        conn.execute("update materials set status='uploading' where id=%s", (material,))
    with pytest.raises(MaterializeError, match="not ready"):
        material_runtime_block(
            job_db.dsn_identity,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": material}),
            storage=storage,
        )


def test_runtime_block_without_configured_storage(
    job_db, material, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.app.services.material_cache.build_s3_storage", lambda: None)

    with pytest.raises(MaterializeError, match="storage is not configured"):
        material_runtime_block(
            job_db.dsn_identity,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": material}),
        )


def test_claim_block_carries_presigned_url_without_storage_key(job_db, material, storage) -> None:
    block = material_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        _job({"type": "material", "material_id": material}),
        storage=storage,
    )

    assert block is not None
    assert block["material_id"] == material
    assert block["content_hash"] == HASH
    assert block["size_bytes"] == len(PAYLOAD)
    assert block["download_url"].startswith("https://s3.test/download/")
    assert "storage_key" not in block
    assert storage.presigned_gets == [STORAGE_KEY]
    # 非 material 输入不下发材料块。
    assert (
        material_claim_block(
            job_db.dsn_identity,
            WORKSPACE_ID,
            _job({"type": "ref", "external_id": "q-1"}),
            storage=storage,
        )
        is None
    )


def test_runtime_block_detects_post_complete_overwrite_via_etag(
    job_db, storage, tmp_path: Path
) -> None:
    """#1279 验收项（Host 链故障注入，目标洞的原生形态）：无 content_hash
    的 ready 材料行（object_etag 是唯一内容身份）在 complete 后被同尺寸
    静默覆盖——cache miss 的 GET 带 If-Match，S3 412 检出为
    MaterializeError，毒化字节不落缓存。etag=''（存量行）维持 size-only
    旧语义（本文件其余用例覆盖；存量行身份建立拆至 #1316）。"""
    from tests.fakes.storage import etag_of

    key = f"{WORKSPACE_ID}/mat-nh/g0/mat-nh/file.bin"
    payload = b"hashless-material"
    storage.objects[key] = payload
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'MatCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename,"
            " content_type, size_bytes, storage_key, status, created_by, object_etag)"
            " values ('mat-nh', %s, '', 'file.bin', 'application/octet-stream',"
            " %s, %s, 'ready', 'user-1', %s)",
            (WORKSPACE_ID, len(payload), key, etag_of(payload)),
        )
    job = _job({"type": "material", "material_id": "mat-nh"})
    # complete 后同尺寸覆盖（已签发 URL 的 1h 窗口残留场景）。
    storage.objects[key] = b"y" * len(payload)

    with pytest.raises(MaterializeError, match="ETag mismatch"):
        material_runtime_block(job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage)
    assert not (tmp_path / "ma" / "mat-nh-v2g0").exists(), "毒化字节不得落缓存"

    # 恢复正确字节（ETag 重新匹配）后正常物化。
    storage.objects[key] = payload
    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None and Path(block["path"]).read_bytes() == payload


def test_legacy_unbound_hashless_row_is_size_only(job_db, storage, tmp_path: Path) -> None:
    """#1312 方案 B：存量无 hash 行（object_etag=''，身份未建立）在 Host
    物化与 claim 描述符两个入口都维持 base 的 size-only 语义——不 fail-closed，
    GET 不带条件头；描述符照常下发（object_etag=''）。"""
    key = f"{WORKSPACE_ID}/mat-legacy/g0/mat-legacy/file.bin"
    payload = b"legacy-hashless"
    storage.objects[key] = payload
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'MatCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename,"
            " content_type, size_bytes, storage_key, status, created_by)"
            " values ('mat-legacy', %s, '', 'file.bin', 'application/octet-stream',"
            " %s, %s, 'ready', 'user-1')",
            (WORKSPACE_ID, len(payload), key),
        )
    job = _job({"type": "material", "material_id": "mat-legacy"})

    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None and Path(block["path"]).read_bytes() == payload

    descriptor = material_claim_block(job_db.dsn_identity, WORKSPACE_ID, job, storage=storage)
    assert descriptor is not None
    assert descriptor["object_etag"] == ""


def test_hashless_cache_address_follows_repair_generation(job_db, storage, tmp_path: Path) -> None:
    """codex r6 场景转正：无 hash 行的缓存地址 = material_id + 代次——修复
    （代次 +1）后旧 gen 地址的（毒化）缓存不再命中，消费走 verified opener
    重新下载。mutation 互证：摘代次段（裸 id 取址）→ 本测试红（毒化字节
    被旧地址命中返回）。"""
    from server.app.services.materials import MaterialsService

    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'MatCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    service = MaterialsService(job_db.dsn_identity, storage)
    payload = b"goodbyte"
    result = service.presign(WORKSPACE_ID, filename="f.bin", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    job = _job({"type": "material", "material_id": material_id})

    block = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert block is not None
    first_path = Path(block["path"])
    assert first_path.name == f"{material_id}-v2g0"
    assert storage.opened == 1

    # 模拟 codex 场景：旧 gen 地址的缓存里是被毒化的字节（旧 Worker 无核验
    # 缓存的残留），对象也被同尺寸覆盖。
    first_path.write_bytes(b"poisoned")  # 同长（8 字节）
    storage.objects[key0] = b"poisoned"

    # 修复 + 用签发 URL 重传 + complete(代次)。
    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    storage.objects[str(outcome["storage_key"])] = payload
    service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)

    again = material_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage
    )
    assert again is not None
    second_path = Path(again["path"])
    assert second_path.name == f"{material_id}-v2g1"
    assert second_path != first_path
    assert second_path.read_bytes() == payload
    assert storage.opened == 2, "代次推进后不得命中旧 gen 地址"


def test_response_snapshot_gate_catches_repair_complete_toctou(job_db, storage) -> None:
    """#1279 codex final P1（single/uploading）：admission 后 repair→complete
    落新 etag 的 TOCTOU——响应描述符快照复核拦下（v5 拒、v6 放行）。
    mutation 互证：摘掉响应复核 → 本测试红。"""
    from server.app.services.materials import MaterialsService
    from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

    payload = b"toctou-bytes"
    service = MaterialsService(job_db.dsn_identity, storage)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Toctou') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="t.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    job = _job({"type": "material", "material_id": material_id})

    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    storage.objects[str(outcome["storage_key"])] = payload
    service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)

    with pytest.raises(MaterializeError, match="protocol v6"):
        material_claim_block(
            job_db.dsn_identity,
            WORKSPACE_ID,
            job,
            storage=storage,
            worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1,
        )
    block = material_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        job,
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )
    assert block is not None and block["object_etag"]


def test_response_snapshot_gate_catches_failed_retry_toctou(job_db, storage) -> None:
    """#1279 codex final P1（single/failed-retry）：repair 后 bare complete
    失败（缺对象）→ failed+etag=''，再 complete 成功落新 etag——同一响应
    快照竞态，v5 拒、v6 放行。"""
    from server.app.services.materials import MaterialsService
    from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

    payload = b"failed-retry"
    service = MaterialsService(job_db.dsn_identity, storage)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Toctou') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="f.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    job = _job({"type": "material", "material_id": material_id})

    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    new_key = str(outcome["storage_key"])
    # bare complete：新 key 缺对象 → failed + etag=''。
    from server.app.services.materials import MaterialVerificationError

    with pytest.raises(MaterialVerificationError, match="missing"):
        service.complete(WORKSPACE_ID, material_id)
    assert service.get(WORKSPACE_ID, material_id)["status"] == "failed"
    # 补齐对象后 complete 成功 → ready + 新 etag（修复链 TOCTOU 格）。
    storage.objects[new_key] = payload
    service.complete(WORKSPACE_ID, material_id)

    with pytest.raises(MaterializeError, match="protocol v6"):
        material_claim_block(
            job_db.dsn_identity,
            WORKSPACE_ID,
            job,
            storage=storage,
            worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1,
        )
    block = material_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        job,
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )
    assert block is not None and block["object_etag"]


def test_claim_block_carries_object_etag(job_db, material, storage) -> None:
    """#1279：claim 描述符携带行记录的 object_etag（Worker 侧 If-Match 核验）
    与 upload_generation（无 hash 行缓存寻址的代次段，与 URL 同行快照）。"""
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='etag-1' where id=%s", (material,))

    block = material_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        _job({"type": "material", "material_id": material}),
        storage=storage,
    )

    assert block is not None
    assert block["object_etag"] == "etag-1"
    assert block["upload_generation"] == 0


def test_runtime_block_facade_passthrough_no_getattr_escape(
    job_db, material, storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#187 getattr 逃逸收口：connect source 是 facade 形态时必须原样
    直通给连接层（ConnectSource），不得经 getattr 抽回裸 DSN。"""
    import server.app.services.material_cache as module
    from server.app.db.transaction import read_connection as real_read_connection

    received: list[object] = []

    class FakeFacade:
        dsn_identity = job_db.dsn_identity  # real DSN behind the facade

        @property
        def path(self):  # pragma: no cover - must not be touched
            raise AssertionError("facade must pass through, not unwrap .path")

    def recording_read_connection(source):
        received.append(source)
        return real_read_connection(source)

    monkeypatch.setattr(module, "read_connection", recording_read_connection)
    block = material_runtime_block(
        FakeFacade(),
        tmp_path,
        WORKSPACE_ID,
        _job({"type": "material", "material_id": material}),
        storage=storage,
    )

    assert block is not None
    assert block["material_id"] == material
    assert received and isinstance(received[0], FakeFacade)
