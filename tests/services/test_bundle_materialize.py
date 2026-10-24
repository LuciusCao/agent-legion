"""Host bundle 物化：bundle_runtime_block / bundle_claim_block（design §6.2, #156）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.app.services.material_bundle_cache import (
    bundle_runtime_block,
    is_bundle_input,
    prefetch_bundle_block,
)
from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.material_cache import material_runtime_block
from server.app.services.material_claim_descriptors import bundle_claim_block
from shared.material_bundle import bundle_address
from shared.material_cache import MaterializeError
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION
from tests.fakes.storage import FakeObjectStorage

WORKSPACE_ID = "ws-bundle-cache"
PAYLOAD_A = b"bundle-member-a" * 20
PAYLOAD_B = b"bundle-member-b" * 30
HASH_A = hashlib.sha256(PAYLOAD_A).hexdigest()
HASH_B = hashlib.sha256(PAYLOAD_B).hexdigest()
KEY_A = f"{WORKSPACE_ID}/{HASH_A}/a.txt"
KEY_B = f"{WORKSPACE_ID}/{HASH_B}/b.txt"


def _fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage(objects={KEY_A: PAYLOAD_A, KEY_B: PAYLOAD_B})


FakeStorage = _fake_storage


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def bundle(job_db, storage) -> dict:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name)"
            " values (%s, 'BundleCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        for material_id, content_hash, size, storage_key in (
            ("mat-a", HASH_A, len(PAYLOAD_A), KEY_A),
            ("mat-b", HASH_B, len(PAYLOAD_B), KEY_B),
        ):
            conn.execute(
                "insert into materials(id, workspace_id, content_hash, filename,"
                " content_type, size_bytes, storage_key, status)"
                " values (%s, %s, %s, 'x.txt', 'text/plain', %s, %s, 'ready')",
                (material_id, WORKSPACE_ID, content_hash, size, storage_key),
            )
    service = MaterialBundlesService(job_db.dsn_identity)
    return service.create(
        WORKSPACE_ID,
        name="folder",
        members=[
            {"material_id": "mat-a", "path": "a.txt"},
            {"material_id": "mat-b", "path": "sub/b.txt"},
        ],
    )


def _job(bundle_id: str) -> dict:
    return {"input_json": json.dumps({"type": "bundle", "bundle_id": bundle_id})}


def test_is_bundle_input() -> None:
    assert is_bundle_input({"input_json": {"type": "bundle", "bundle_id": "b"}})
    assert not is_bundle_input({"input_json": {"type": "material", "material_id": "m"}})
    assert not is_bundle_input({"input_json": "not-json"})


def test_runtime_block_materializes_directory_tree(job_db, bundle, storage, tmp_path: Path) -> None:
    block = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )

    assert block is not None
    assert block["kind"] == "bundle"
    assert block["material_id"] == bundle["id"]
    assert block["filename"] == "folder"
    tree = Path(block["path"])
    assert tree.is_dir()
    assert (tree / "a.txt").read_bytes() == PAYLOAD_A
    assert (tree / "sub" / "b.txt").read_bytes() == PAYLOAD_B
    assert [entry["path"] for entry in block["entries"]] == ["a.txt", "sub/b.txt"]
    # Bundle 地址 = 排序清单的 sha256，Host/Worker 同规则。
    expected = bundle_address([(HASH_A, "a.txt"), (HASH_B, "sub/b.txt")])
    assert block["content_hash"] == expected
    assert tree == tmp_path / expected[:2] / expected


def test_runtime_block_hits_cache_on_second_call(job_db, bundle, storage, tmp_path: Path) -> None:
    first = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )
    opened_after_first = storage.opened
    second = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )

    assert second["path"] == first["path"]
    assert storage.opened == opened_after_first


def test_runtime_block_pins_all_members_against_eviction(
    job_db, bundle, storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算装不下整个 bundle 时，成员 i+1 的淘汰回合不得删掉成员 i（#156）。"""
    monkeypatch.setenv("AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES", "1")

    block = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )

    tree = Path(block["path"])
    assert (tree / "a.txt").read_bytes() == PAYLOAD_A
    assert (tree / "sub" / "b.txt").read_bytes() == PAYLOAD_B
    # 成员缓存文件也全部存活（pin 保住，缓存暂时超预算）。
    assert (tmp_path / HASH_A[:2] / HASH_A).exists()
    assert (tmp_path / HASH_B[:2] / HASH_B).exists()


def test_runtime_block_returns_none_for_non_bundle(job_db, storage, tmp_path: Path) -> None:
    job = {"input_json": json.dumps({"type": "material", "material_id": "mat-a"})}
    assert (
        bundle_runtime_block(job_db.dsn_identity, tmp_path, WORKSPACE_ID, job, storage=storage)
        is None
    )
    # 单文件物化对 bundle 输入同样返回 None（分发在 prefetch/claim 层）。
    assert (
        material_runtime_block(
            job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job("b-1"), storage=storage
        )
        is None
    )


def test_runtime_block_fails_closed_when_member_not_ready(
    job_db, bundle, storage, tmp_path: Path
) -> None:
    with job_db.connect() as conn:
        conn.execute("update materials set status='expired' where id='mat-b'")

    with pytest.raises(MaterializeError, match="not ready"):
        bundle_runtime_block(
            job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
        )


def test_runtime_block_unknown_bundle_is_readable_error(job_db, storage, tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="not found"):
        bundle_runtime_block(
            job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job("b-missing"), storage=storage
        )


def test_bundle_response_snapshot_gate_catches_repair_complete_toctou(job_db, storage) -> None:
    """#1279 codex final P1（bundle/uploading）：bundle 成员 repair→complete
    落新 etag 的 TOCTOU——bundle_claim_block 的响应快照复核拦下（v5 拒、
    v6 放行）。"""
    from server.app.services.materials import MaterialsService
    from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

    payload = b"bundle-toctou"
    service = MaterialsService(job_db.dsn_identity, storage)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'ToctouB') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="b.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    bundle = MaterialBundlesService(job_db.dsn_identity).create(
        WORKSPACE_ID, name="folder", members=[{"material_id": material_id, "path": "b.txt"}]
    )
    job = _job(bundle["id"])

    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    storage.objects[str(outcome["storage_key"])] = payload
    service.complete(WORKSPACE_ID, material_id, expected_upload_generation=1)

    with pytest.raises(MaterializeError, match="protocol v6"):
        bundle_claim_block(
            job_db.dsn_identity,
            WORKSPACE_ID,
            job,
            storage=storage,
            worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1,
        )
    block = bundle_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        job,
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )
    assert block is not None and block["entries"][0]["object_etag"]


def test_bundle_response_snapshot_gate_catches_failed_retry_toctou(job_db, storage) -> None:
    """#1279 codex final P1（bundle/failed-retry）：repair 后 bare complete
    失败 → failed+etag=''，再 complete 成功落新 etag——v5 拒、v6 放行。"""
    from server.app.services.materials import MaterialsService, MaterialVerificationError
    from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

    payload = b"bundle-failed"
    service = MaterialsService(job_db.dsn_identity, storage)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'ToctouB') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="f.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    bundle = MaterialBundlesService(job_db.dsn_identity).create(
        WORKSPACE_ID, name="folder", members=[{"material_id": material_id, "path": "f.txt"}]
    )
    job = _job(bundle["id"])

    outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
    new_key = str(outcome["storage_key"])
    with pytest.raises(MaterialVerificationError, match="missing"):
        service.complete(WORKSPACE_ID, material_id)
    assert service.get(WORKSPACE_ID, material_id)["status"] == "failed"
    storage.objects[new_key] = payload
    service.complete(WORKSPACE_ID, material_id)

    with pytest.raises(MaterializeError, match="protocol v6"):
        bundle_claim_block(
            job_db.dsn_identity,
            WORKSPACE_ID,
            job,
            storage=storage,
            worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1,
        )
    block = bundle_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        job,
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )
    assert block is not None and block["entries"][0]["object_etag"]


def test_production_wrapper_bundle_toctou_gate(job_db, storage) -> None:
    """#1279 codex final r2：bundle 生产转发层 `material_claim_block` 把
    worker_protocol_version 继续传给 bundle_claim_block——版本漏传即被
    None 旁路，本测试钉住该接线（mutation：摘 wrapper 透传 → 本测试红）。"""
    from server.app.services.material_claim_descriptors import material_claim_block
    from server.app.services.materials import MaterialsService
    from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION

    payload = b"wrapper-toctou"
    service = MaterialsService(job_db.dsn_identity, storage)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'ToctouW') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    result = service.presign(WORKSPACE_ID, filename="w.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key0, _) = storage.presigned[0]
    storage.objects[key0] = payload
    service.complete(WORKSPACE_ID, material_id)
    bundle = MaterialBundlesService(job_db.dsn_identity).create(
        WORKSPACE_ID, name="folder", members=[{"material_id": material_id, "path": "w.txt"}]
    )
    job = _job(bundle["id"])

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
    assert block is not None and block["kind"] == "bundle"
    assert block["entries"][0]["object_etag"]


def test_claim_block_presigns_each_member_without_storage_key(job_db, bundle, storage) -> None:
    block = bundle_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        _job(bundle["id"]),
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )

    assert block is not None
    assert block["kind"] == "bundle"
    assert len(block["entries"]) == 2
    assert sorted(storage.presigned_gets) == sorted([KEY_A, KEY_B])
    for entry in block["entries"]:
        assert entry["download_url"].startswith("https://s3.test/download/")
        assert entry["material_id"] in ("mat-a", "mat-b")
    assert "storage_key" not in json.dumps(block)


def test_bundle_member_address_follows_generation(job_db, storage, tmp_path: Path) -> None:
    """#1279 codex r6（Host bundle 成员同格）：无 hash 成员的缓存地址带
    代次——成员代次推进后地址变化、重新下载，树地址随成员地址联动变化。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name)"
            " values (%s, 'BundleCache') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename,"
            " content_type, size_bytes, storage_key, status)"
            " values ('mat-h', %s, '', 'h.txt', 'text/plain', 4, %s, 'ready')",
            (WORKSPACE_ID, f"{WORKSPACE_ID}/mat-h/g0/mat-h/h.txt"),
        )
    storage.objects[f"{WORKSPACE_ID}/mat-h/g0/mat-h/h.txt"] = b"hash"
    service = MaterialBundlesService(job_db.dsn_identity)
    bundle = service.create(
        WORKSPACE_ID, name="folder", members=[{"material_id": "mat-h", "path": "h.txt"}]
    )

    block = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )
    assert block is not None
    first_tree = block["path"]
    assert (tmp_path / "ma" / "mat-h-v2g0").is_file()
    assert storage.opened == 1

    # 成员代次推进（修复语义）：地址变化 → 重新下载，树地址联动变化。
    with job_db.connect() as conn:
        conn.execute("update materials set upload_generation=1 where id='mat-h'")
    again = bundle_runtime_block(
        job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )
    assert again is not None
    assert again["path"] != first_tree
    assert (tmp_path / "ma" / "mat-h-v2g1").is_file()
    assert storage.opened == 2, "代次推进后不得命中旧 gen 地址"


def test_runtime_block_detects_member_overwrite_via_etag(
    job_db, bundle, storage, tmp_path: Path
) -> None:
    """#1279 验收项（Host bundle 链故障注入）：成员材料 complete 后被同尺寸
    静默覆盖——行记录的 object_etag 使该成员的 GET 带 If-Match，412 检出为
    MaterializeError，毒化字节不落缓存。"""
    from tests.fakes.storage import etag_of

    with job_db.connect() as conn:
        conn.execute("update materials set object_etag=%s where id='mat-a'", (etag_of(PAYLOAD_A),))
    storage.objects[KEY_A] = b"x" * len(PAYLOAD_A)

    with pytest.raises(MaterializeError, match="ETag mismatch"):
        bundle_runtime_block(
            job_db.dsn_identity, tmp_path, WORKSPACE_ID, _job(bundle["id"]), storage=storage
        )
    assert not (tmp_path / HASH_A[:2] / HASH_A).exists(), "毒化字节不得落缓存"


def test_claim_block_carries_member_object_etag(job_db, bundle, storage) -> None:
    """#1279：bundle claim 描述符逐成员携带行记录的 object_etag。"""
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='etag-a' where id='mat-a'")

    block = bundle_claim_block(
        job_db.dsn_identity,
        WORKSPACE_ID,
        _job(bundle["id"]),
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )

    assert block is not None
    by_id = {entry["material_id"]: entry for entry in block["entries"]}
    assert by_id["mat-a"]["object_etag"] == "etag-a"
    assert by_id["mat-b"]["object_etag"] == ""


class _FakeExecutor:
    """prefetch_bundle_block 需要的最小 executor 面（dispatch 接线）。"""

    def __init__(self, job_db, cache_root: Path, storage: FakeStorage) -> None:
        self.job_db = job_db
        self._materials_cache_root = cache_root
        self._storage = storage

    def _object_store(self) -> FakeStorage:
        return self._storage


def test_prefetch_bundle_block_wires_executor_dispatch(
    job_db, bundle, storage, tmp_path: Path
) -> None:
    """dispatch 实际调用路径：DSN / cache root / storage 全部取自 executor。"""
    executor = _FakeExecutor(job_db, tmp_path, storage)

    block = prefetch_bundle_block(executor, _job(bundle["id"]), WORKSPACE_ID)

    assert block is not None
    assert block["kind"] == "bundle"
    tree = Path(block["path"])
    assert (tree / "a.txt").read_bytes() == PAYLOAD_A
    assert (tree / "sub" / "b.txt").read_bytes() == PAYLOAD_B


def test_prefetch_bundle_block_returns_none_for_non_bundle(job_db, storage, tmp_path: Path) -> None:
    executor = _FakeExecutor(job_db, tmp_path, storage)
    job = {"input_json": json.dumps({"type": "material", "material_id": "mat-a"})}
    assert prefetch_bundle_block(executor, job, WORKSPACE_ID) is None


def test_prefetch_bundle_block_fails_closed_without_job_db(bundle, storage, tmp_path: Path) -> None:
    executor = _FakeExecutor(None, tmp_path, storage)
    with pytest.raises(MaterializeError, match="job database"):
        prefetch_bundle_block(executor, _job(bundle["id"]), WORKSPACE_ID)


def test_prefetch_bundle_block_facade_passthrough_no_getattr_escape(
    job_db, bundle, storage, tmp_path: Path, monkeypatch
) -> None:
    """#187 getattr 逃逸收口：executor.job_db 是 facade 形态时必须原样直通
    给连接层（ConnectSource），不得经 getattr(job_db, "path") 抽回裸 DSN。"""
    import server.app.services.material_bundle_cache as module
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
    executor = _FakeExecutor(FakeFacade(), tmp_path, storage)

    block = prefetch_bundle_block(executor, _job(bundle["id"]), WORKSPACE_ID)

    assert block is not None
    assert block["kind"] == "bundle"
    assert received and isinstance(received[0], FakeFacade)
