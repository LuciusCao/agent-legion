"""AgentCompletionHandler 接收 Worker 直传 S3 的产物引用（#160 D12）。

新形态 ref → 先全部 HEAD 核验，再统一下载落 job_dir（原子 rename，只落
expected_outputs 白名单）+ record_remote 登记；任一失败整个 result 判
failed，且不留半应用状态。旧形态 str ref 的 add_ref 路径不变（回归由
tests/test_agent_completion_validation.py 覆盖）。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any, BinaryIO

from server.app.agent_completion import AgentCompletionHandler, AgentOutcome
from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectHead
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b"remote-artifact-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()
KEY = "jobs/ws-1/job-1/out.json"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0

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
        self.put_calls += 1
        self.objects[storage_key] = data

    def put_stream(self, storage_key: str, stream: BinaryIO, size_bytes: int) -> None:
        self.put_calls += 1
        self.objects[storage_key] = stream.read()

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


class _StubJobDb:
    def __init__(self, job: dict[str, Any]) -> None:
        self._job = job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._job if job_id == self._job["id"] else None


class _StubLeases:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job_db = _StubJobDb(job)
        self.data_dir = None
        self.results: list[Any] = []

    def finish(self, lease_id: str, result: Any) -> bool:
        self.results.append(result)
        return True


class _StubArtifactStore:
    def __init__(self) -> None:
        self.refs: list[tuple[str, str, str, str]] = []

    def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
        self.refs.append((job_id, node_key, name, digest))


def _remote_ref(key: str = KEY, size: int | None = None, content_hash: str = HASH) -> dict:
    return {
        "storage_key": key,
        "size_bytes": len(PAYLOAD) if size is None else size,
        "content_hash": content_hash,
    }


def _make_handler(
    tmp_path: Path, storage: FakeStorage | None
) -> tuple[AgentCompletionHandler, _StubLeases, _StubArtifactStore, JobArtifactObjectStore, Path]:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-1', 'ws', 'demo_workflow')"
            " on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values ('job-1', 'ws-1', 'wf', 's', 's1', 't', 'pending', 'd')"
        )
    jobs_dir = tmp_path / "jobs"
    job = {"id": "job-1", "workspace_id": "ws-1", "storage_dir": "jobs/ws/job-1"}
    job_dir = jobs_dir / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    leases = _StubLeases(job)
    artifact_store = _StubArtifactStore()
    object_store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    handler = AgentCompletionHandler(
        leases,  # type: ignore[arg-type]
        artifact_store,  # type: ignore[arg-type]
        jobs_dir,
        tmp_path / "bundles",
        skill_manager=None,
        object_store=object_store,
    )
    return handler, leases, artifact_store, object_store, job_dir


def _finish(handler: AgentCompletionHandler, artifacts: dict[str, Any]) -> None:
    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"]},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )


def test_finish_remote_ref_downloads_and_registers(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[KEY] = PAYLOAD
    handler, leases, artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert leases.results[0].produced_artifacts == ("out.json",)
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["storage_key"] == KEY
    assert row["content_hash"] == HASH
    assert artifact_store.refs == []  # 新通道不登记 CAS ref
    assert storage.put_calls == 0  # 已在 S3，不做 D12 镜像重传


def test_finish_remote_ref_missing_object_fails(tmp_path: Path) -> None:
    storage = FakeStorage()  # 对象不存在：HEAD 核验失败
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "missing" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is None


def test_finish_remote_ref_size_mismatch_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[KEY] = PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(size=len(PAYLOAD) + 1)})

    result = leases.results[0]
    assert result.status == "failed"
    assert "size" in result.error_message


def test_finish_remote_ref_unexpected_key_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[KEY] = PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(key="jobs/ws-1/other-job/out.json")})

    result = leases.results[0]
    assert result.status == "failed"
    assert "storage key" in result.error_message


def test_finish_remote_ref_hash_mismatch_on_download_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[KEY] = PAYLOAD
    handler, leases, _, _, job_dir = _make_handler(tmp_path, storage)
    ref = _remote_ref(content_hash="0" * 64)  # HEAD 通过，下载字节对不上

    _finish(handler, {"out.json": ref})

    result = leases.results[0]
    assert result.status == "failed"
    assert "hash mismatch" in result.error_message
    assert not (job_dir / "out.json").exists()


def test_finish_remote_refs_are_all_verified_before_any_apply(tmp_path: Path) -> None:
    """第二个 ref HEAD 失败时，第一个 ref 不得落盘或登记（无半应用状态）。"""
    storage = FakeStorage()
    first_key = "jobs/ws-1/job-1/a.json"
    storage.objects[first_key] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    artifacts = {
        "a.json": _remote_ref(key=first_key),
        "out.json": _remote_ref(),  # 对象不存在
    }

    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["a.json", "out.json"]},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )

    assert leases.results[0].status == "failed"
    assert not (job_dir / "a.json").exists()
    assert object_store.lookup("job-1", "a.json") is None


def test_finish_remote_ref_without_object_storage_fails(tmp_path: Path) -> None:
    handler, leases, _, _, _ = _make_handler(tmp_path, None)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "not configured" in result.error_message


def test_finish_mixed_refs_registers_both_channels(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[KEY] = PAYLOAD
    legacy_hash = "b" * 64
    handler, leases, artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(
        handler,
        {"out.json": _remote_ref(), "extra.json": f"sha256:{legacy_hash}"},
    )

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    assert object_store.lookup("job-1", "out.json") is not None
    assert artifact_store.refs == [("job-1", "node_a", "extra.json", legacy_hash)]
