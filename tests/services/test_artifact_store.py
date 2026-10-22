from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.artifact_store import (
    GC_GRACE_SECONDS,
    ArtifactNotFoundError,
    ArtifactStore,
)
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def store(tmp_path):
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    return ArtifactStore(tmp_path / "artifacts", db_path)


def _make_job(db_path, job_id: str) -> None:
    """artifact_refs.job_id has a real FK to jobs(id); create a minimal job row."""
    with write_transaction(db_path) as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('ws', 'ws') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, "
            " title, status, storage_dir) values (%s, 'ws', 's', 's1', 't', 'pending', 'd')",
            (job_id,),
        )


def test_put_open_round_trip(store):
    h = store.put(b"hello artifact")
    assert len(h) == 64
    assert store.open(h).read_bytes() == b"hello artifact"


def test_put_dedupes_by_hash(store):
    h1 = store.put(b"same content")
    h2 = store.put(b"same content")
    assert h1 == h2
    assert store.open(h1).read_bytes() == b"same content"


def test_open_rejects_malformed_hash(store):
    with pytest.raises(ArtifactNotFoundError):
        store.open("../etc/passwd")
    with pytest.raises(ArtifactNotFoundError):
        store.open("zz" * 32)


def test_open_missing_raises(store):
    with pytest.raises(ArtifactNotFoundError):
        store.open("ab" * 32)


def test_no_partial_file_after_failed_write(store, monkeypatch):
    import os

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.put(b"doomed")
    monkeypatch.setattr(os, "replace", real_replace)
    # 崩溃后不产生可见成品，也不留 staging 残骸影响重试
    h = store.put(b"doomed")
    assert store.open(h).read_bytes() == b"doomed"
    assert not list((store.root / ".staging").glob("*"))


def test_refs_lifecycle(store, tmp_path):
    _make_job(TEST_DATABASE_URL, "job-1")
    _make_job(TEST_DATABASE_URL, "job-2")
    h = store.put(b"referenced")
    store.add_ref("job-1", "node-a", "output.json", h)
    store.add_ref("job-1", "node-a", "output.json", h)  # 幂等
    store.add_ref("job-2", "node-b", "output.json", h)
    assert len(store.refs_for_job("job-1")) == 1
    # #1248：job 行仍在（删除提交后同源重建占用了同一 id）时，按 id 的 refs
    # 清扫绝不生效——这些行归新 job 所有。
    assert store.delete_refs_for_job("job-1") == []
    assert len(store.refs_for_job("job-1")) == 1
    assert len(store.refs_for_job("job-2")) == 1
    # 删除路径的收尾：jobs 行已删（FK 级联收走 refs）后清扫是幂等空操作。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from jobs where id in ('job-1', 'job-2')")
    assert store.delete_refs_for_job("job-1") == []
    assert store.refs_for_job("job-1") == []
    assert store.refs_for_job("job-2") == []
    # h 不再被任何 job 引用：过了 GC 宽限窗后物理删除。
    past_grace = datetime.now(UTC) + timedelta(seconds=GC_GRACE_SECONDS + 60)
    assert store.delete_unreferenced([h], now=past_grace) == 1
    with pytest.raises(ArtifactNotFoundError):
        store.open(h)


def test_delete_refs_sweeps_refs_whose_job_row_is_gone(store):
    """正向路径：jobs 行不在而 refs 残留（级联缺失的存量/损坏形态）时才真正
    删除，并返回不再被任何 job 引用的 hash。FK 拦着正常写入，用
    session_replication_role=replica 模拟（非 superuser 角色跳过）。"""
    h = store.put(b"orphaned")
    try:
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute("set session_replication_role = replica")
            conn.execute(
                "insert into artifact_refs(job_id, node_key, name, hash) values (%s, %s, %s, %s)",
                ("gone-job", "node-a", "out.json", h),
            )
    except Exception as exc:  # 测试角色非 superuser，无法绕过 FK
        pytest.skip(f"cannot simulate pre-cascade orphan refs: {exc}")
    orphaned = store.delete_refs_for_job("gone-job")
    assert orphaned == [h]
    assert store.refs_for_job("gone-job") == []


def test_put_reasserts_catalog_row_for_existing_blob(store):
    _make_job(TEST_DATABASE_URL, "job-1")
    h = store.put(b"content")
    # Simulate the crash window: blob on disk, catalog row missing.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from artifacts where hash = %s", (h,))
    assert store.put(b"content") == h
    # artifact_refs.hash FK is satisfied again, so add_ref must not fail.
    store.add_ref("job-1", "node-a", "out.json", h)
    assert [ref["hash"] for ref in store.refs_for_job("job-1")] == [h]


def test_delete_unreferenced_skips_young_artifacts(store):
    h = store.put(b"fresh")
    assert store.delete_unreferenced([h]) == 0
    assert store.open(h).read_bytes() == b"fresh"
    past_grace = datetime.now(UTC) + timedelta(seconds=GC_GRACE_SECONDS + 60)
    assert store.delete_unreferenced([h], now=past_grace) == 1
    with pytest.raises(ArtifactNotFoundError):
        store.open(h)


def test_delete_unreferenced_tolerates_unlink_failure(store, monkeypatch):
    h = store.put(b"doomed-blob")
    past_grace = datetime.now(UTC) + timedelta(seconds=GC_GRACE_SECONDS + 60)

    def boom(self, *args, **kwargs):
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(Path, "unlink", boom)
    assert store.delete_unreferenced([h], now=past_grace) == 1
    monkeypatch.undo()
    # The committed row deletion stands; the blob file remains as an orphan.
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select hash from artifacts where hash = %s", (h,)).fetchone()
    assert row is None
    assert (store.root / h[:2] / h).is_file()


def test_connect_source_property_passthrough(tmp_path):
    """#187 命名债收口：store 持 facade 时 ``connect_source`` 原样交出对象
    本身，孤儿 GC 等消费方直通给 read_connection，不抽 DSN。"""
    init_db(TEST_DATABASE_URL)

    class FakeFacade:
        dsn_identity = TEST_DATABASE_URL

        @property
        def path(self):  # pragma: no cover - must not be touched
            raise AssertionError("facade must pass through, not unwrap .path")

    facade = FakeFacade()
    store = ArtifactStore(tmp_path / "artifacts", facade)  # type: ignore[arg-type]

    assert store.connect_source is facade
    # The store round-trips real writes through the facade-backed source.
    h = store.put(b"facade-backed")
    assert store.open(h).read_bytes() == b"facade-backed"
