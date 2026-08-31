"""Host bundle 物化：bundle_runtime_block / bundle_claim_block（design §6.2, #156）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.app.services.material_bundle_cache import (
    bundle_claim_block,
    bundle_runtime_block,
    is_bundle_input,
    prefetch_bundle_block,
)
from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.material_cache import material_runtime_block
from shared.material_bundle import bundle_address
from shared.material_cache import MaterializeError
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
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'BundleCache', 'demo_workflow') on conflict(id) do nothing",
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


def test_claim_block_presigns_each_member_without_storage_key(job_db, bundle, storage) -> None:
    block = bundle_claim_block(
        job_db.dsn_identity, WORKSPACE_ID, _job(bundle["id"]), storage=storage
    )

    assert block is not None
    assert block["kind"] == "bundle"
    assert len(block["entries"]) == 2
    assert sorted(storage.presigned_gets) == sorted([KEY_A, KEY_B])
    for entry in block["entries"]:
        assert entry["download_url"].startswith("https://s3.test/download/")
        assert entry["material_id"] in ("mat-a", "mat-b")
    assert "storage_key" not in json.dumps(block)


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
