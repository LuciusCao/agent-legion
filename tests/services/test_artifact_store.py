from __future__ import annotations

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.artifact_store import (
    ArtifactNotFoundError,
    ArtifactStore,
)


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return ArtifactStore(tmp_path / "artifacts", db_path)


def _make_job(db_path, job_id: str) -> None:
    """artifact_refs.job_id has a real FK to jobs(id); create a minimal job row."""
    with write_transaction(db_path) as conn:
        conn.execute("insert or ignore into workspaces(id, name) values ('ws', 'ws')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values (?, 'ws', 'wf', 's', 's1', 't', 'pending', 'd')",
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
    _make_job(tmp_path / "test.db", "job-1")
    _make_job(tmp_path / "test.db", "job-2")
    h = store.put(b"referenced")
    store.add_ref("job-1", "node-a", "output.json", h)
    store.add_ref("job-1", "node-a", "output.json", h)  # 幂等
    store.add_ref("job-2", "node-b", "output.json", h)
    assert len(store.refs_for_job("job-1")) == 1
    orphaned = store.delete_refs_for_job("job-1")
    assert orphaned == []  # job-2 仍引用
    orphaned = store.delete_refs_for_job("job-2")
    assert orphaned == [h]
    assert store.delete_unreferenced([h]) == 1
    with pytest.raises(ArtifactNotFoundError):
        store.open(h)
