from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.artifact_orphan_gc import (
    ArtifactOrphanGcThread,
    gc_orphans,
    orphan_stats,
)
from server.app.services.artifact_store import ArtifactStore
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def store(tmp_path):
    init_db(TEST_DATABASE_URL)
    return ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)


def _make_job(job_id: str) -> None:
    """artifact_refs.job_id has a real FK to jobs(id); create a minimal job row."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values (%s, 'ws', 'wf', 's', 's1', 't', 'pending', 'd')",
            (job_id,),
        )


def _age(store: ArtifactStore, hash: str, seconds: float) -> None:
    with write_transaction(store.connect_source) as conn:
        conn.execute(
            "update artifacts set created_at=%s where hash=%s",
            (datetime.now(UTC) - timedelta(seconds=seconds), hash),
        )


def test_orphan_past_grace_is_counted_and_reclaimed(store) -> None:
    h = store.put(b"orphan payload")
    _age(store, h, store.gc_grace_seconds + 60)

    assert orphan_stats(store) == (1, len(b"orphan payload"))
    assert gc_orphans(store) == 1

    assert orphan_stats(store) == (0, 0)
    assert not (store.root / h[:2] / h).exists()


def test_referenced_blob_is_neither_counted_nor_reclaimed(store) -> None:
    _make_job("job-1")
    h = store.put(b"referenced payload")
    store.add_ref("job-1", "node", "out", h)
    _age(store, h, store.gc_grace_seconds + 60)

    assert orphan_stats(store) == (0, 0)
    assert gc_orphans(store) == 0
    assert (store.root / h[:2] / h).is_file()


def test_orphan_inside_grace_window_is_kept(store) -> None:
    store.put(b"fresh payload")

    assert orphan_stats(store) == (0, 0)
    assert gc_orphans(store) == 0


def test_scan_paginates_without_skipping_rows(store, monkeypatch) -> None:
    monkeypatch.setattr("server.app.services.artifact_orphan_gc._SCAN_BATCH", 2)
    hashes = [store.put(f"payload-{index}".encode()) for index in range(3)]
    for h in hashes:
        _age(store, h, store.gc_grace_seconds + 60)

    assert orphan_stats(store)[0] == 3
    assert gc_orphans(store) == 3


def test_thread_reclaims_on_interval_and_stops(store) -> None:
    h = store.put(b"thread payload")
    _age(store, h, store.gc_grace_seconds + 60)
    thread = ArtifactOrphanGcThread(store, interval_seconds=0.05)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while (store.root / h[:2] / h).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not (store.root / h[:2] / h).exists()
    finally:
        thread.stop()


def test_thread_start_is_idempotent(store) -> None:
    thread = ArtifactOrphanGcThread(store, interval_seconds=3600)
    thread.start()
    first = thread._thread
    thread.start()
    assert thread._thread is first
    thread.stop()
    assert thread._thread is None
