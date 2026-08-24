"""Job artifact maintenance：reconciler 行新鲜度 + 淘汰 size 防线（#160 P1-2）。

rerun 产出新字节而上传失败时，旧 (job_id,node_key,name) 清单行不得永久
压制重传（reconciler 比对 size+hash 后 upsert 刷新），也不得过凭旧行
淘汰本地新文件（eviction 只认 size 相符的行）。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services import job_artifact_maintenance
from server.app.services.job_artifact_maintenance import (
    evict_cache_to_capacity,
    reupload_missing,
)
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectHead
from tests.postgres_support import TEST_DATABASE_URL

OLD_PAYLOAD = b"old-bytes"
NEW_PAYLOAD = b"new-bytes-longer"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

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
        self.objects.pop(storage_key, None)

    def copy_object(self, source_key: str, destination_key: str) -> None:
        self.objects[destination_key] = self.objects[source_key]


@pytest.fixture(autouse=True)
def _schema() -> None:
    init_db(TEST_DATABASE_URL)


def _seed_job(status: str = "completed") -> dict[str, Any]:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-1', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values"
            " ('job-1', 'ws-1', 'wf', 's', 's1', 't', %s, 'jobs/ws/job-1')",
            (status,),
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, finished_at)"
            " values ('job-1', 'n1', 'completed', now())"
        )
    return {
        "id": "job-1",
        "workspace_id": "ws-1",
        "workflow_key": "wf",
        "storage_dir": "jobs/ws/job-1",
    }


def _job_db(job: dict[str, Any]) -> Any:
    return SimpleNamespace(path=TEST_DATABASE_URL, get_job=lambda job_id: dict(job))


def _settings(tmp_path: Path) -> Any:
    return SimpleNamespace(jobs_dir=tmp_path / "jobs")


def _definition(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = SimpleNamespace(nodes={"n1": SimpleNamespace(outputs=["out.json"])})
    monkeypatch.setattr(
        job_artifact_maintenance, "definition_from_job_snapshot", lambda job: definition
    )


def test_reconciler_reuploads_when_row_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rerun 新字节 + 旧行（size 不同）→ 重传并 upsert 刷新清单行。"""
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )
    local.write_bytes(NEW_PAYLOAD)  # rerun 产出新字节，上传失败 → 行过期

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 1

    row = store.row_for_node("job-1", "n1", "out.json")
    assert row is not None
    assert int(row["size_bytes"]) == len(NEW_PAYLOAD)
    assert row["content_hash"] == hashlib.sha256(NEW_PAYLOAD).hexdigest()
    assert storage.objects["jobs/ws-1/job-1/out.json"] == NEW_PAYLOAD


def test_reconciler_reuploads_on_same_size_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(b"aaaa")
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )
    local.write_bytes(b"bbbb")  # 同长度新字节：只有 hash 能识别过期

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 1
    assert storage.objects["jobs/ws-1/job-1/out.json"] == b"bbbb"


def test_reconciler_skips_fresh_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 0


def _complete_job() -> dict[str, Any]:
    return _seed_job(status="completed")


def test_eviction_skips_files_whose_size_no_longer_matches(tmp_path: Path) -> None:
    """行在但 size 不符（rerun 新字节未上传）→ 不视为已确认，不淘汰。"""
    job = _complete_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    fresh = job_dir / "out.json"
    fresh.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=fresh
    )
    stale = job_dir / "stale.json"
    stale.write_bytes(b"old")
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="stale.json", local_path=stale
    )
    stale.write_bytes(b"new-longer")  # 行 size=3，本地 size=10 → 不确认

    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 1  # 只有 size 相符的 out.json 被淘汰
    assert not fresh.exists()
    assert stale.is_file()


def test_eviction_removes_confirmed_files(tmp_path: Path) -> None:
    job = _complete_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    confirmed = job_dir / "out.json"
    confirmed.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=confirmed
    )

    assert evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0) == 1
    assert not confirmed.exists()
