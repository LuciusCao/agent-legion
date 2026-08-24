"""Claim manifest 的对象存储产物通道注入（#160 D12）。

inject_artifact_object_block：enabled 时下发 artifact_uploads（presigned
PUT）并把有 job_artifacts 行的 input_artifacts 升级为 {"url", "sha256"}
dict 形态；S3 异常时整体降级、不注入（Worker 走旧 CAS 通道）。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import BinaryIO

from server.app.agent_broker.artifact_object_block import inject_artifact_object_block
from server.app.agent_broker.code_manifest import resolve_code_runtime_context
from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectHead
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b"upstream-artifact"
HASH = hashlib.sha256(PAYLOAD).hexdigest()


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_expiries: list[int] = []
        self.get_expiries: list[int] = []

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        self.put_expiries.append(expires_seconds)
        return f"https://s3.test/upload/{storage_key}?sig=put"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        self.get_expiries.append(expires_seconds)
        return f"https://s3.test/download/{storage_key}?sig=get"

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


class RaisingStorage(FakeStorage):
    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        raise RuntimeError("s3 unreachable")


def _make_job(job_id: str = "job-1", workspace_id: str = "ws-1") -> None:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'ws', 'demo_workflow')"
            " on conflict (id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values (%s, %s, 'wf', 's', 's1', 't', 'pending', 'd')",
            (job_id, workspace_id),
        )


def _manifest() -> dict:
    return {
        "job_id": "job-1",
        "workspace_id": "ws-1",
        "execution_id": "exec-1",
        "expected_outputs": ["out.json"],
        "input_artifacts": {"q.json": f"sha256:{HASH}"},
    }


def test_inject_adds_uploads_and_upgrades_staged_inputs(tmp_path: Path) -> None:
    _make_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    source = tmp_path / "q.json"
    source.write_bytes(PAYLOAD)
    # 上游节点已产出的 artifact：有 job_artifacts 行 → 升级为 presigned GET。
    store.upload(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        local_path=source,
    )
    manifest = _manifest()

    inject_artifact_object_block(store, manifest)

    uploads = manifest["artifact_uploads"]
    assert uploads["out.json"]["storage_key"] == "jobs-staging/ws-1/job-1/exec-1/out.json"
    assert uploads["out.json"]["url"].endswith("jobs-staging/ws-1/job-1/exec-1/out.json?sig=put")
    ref = manifest["input_artifacts"]["q.json"]
    assert ref["sha256"] == HASH
    assert ref["url"].endswith("jobs/ws-1/job-1/q.json?sig=get")
    assert "storage_key" not in ref  # storage_key 不下发


def test_inject_is_idempotent_for_dict_form_inputs(tmp_path: Path) -> None:
    """重复注入（或 input 已是 dict 形态）不嵌套、不报错，URL 重新签发。"""
    _make_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    source = tmp_path / "q.json"
    source.write_bytes(PAYLOAD)
    store.upload(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        local_path=source,
    )
    manifest = _manifest()

    inject_artifact_object_block(store, manifest)
    first = dict(manifest["input_artifacts"]["q.json"])
    inject_artifact_object_block(store, manifest)
    second = manifest["input_artifacts"]["q.json"]

    assert second == first
    assert second["sha256"] == HASH
    assert "storage_key" not in second


def test_inject_keeps_cas_form_for_inputs_without_row() -> None:
    _make_job()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeStorage())
    manifest = _manifest()

    inject_artifact_object_block(store, manifest)

    assert manifest["artifact_uploads"]["out.json"]["storage_key"] == (
        "jobs-staging/ws-1/job-1/exec-1/out.json"
    )
    # 没有 job_artifacts 行（legacy job）→ 保留旧 CAS 形态。
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}


def test_inject_degrades_to_legacy_channel_on_storage_error() -> None:
    _make_job()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, RaisingStorage())
    manifest = _manifest()

    inject_artifact_object_block(store, manifest)

    assert "artifact_uploads" not in manifest  # 不注入，Worker 走旧通道
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}


def test_inject_noop_without_object_storage() -> None:
    manifest = _manifest()
    inject_artifact_object_block(None, manifest)
    inject_artifact_object_block(JobArtifactObjectStore(TEST_DATABASE_URL, None), manifest)
    assert "artifact_uploads" not in manifest
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}


def test_code_claim_rebuild_injects_object_block() -> None:
    """code claim 路径：resolve_code_runtime_context 内存态注入（不落库）。"""
    _make_job()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeStorage())
    manifest = {
        "job_id": "job-1",
        "workspace_id": "ws-1",
        "execution_id": "exec-1",
        "expected_outputs": ["out.json"],
        "input_artifacts": {"q.json": f"sha256:{HASH}"},
        "runtime_context": {"job_id": "job-1", "workspace_id": "ws-1"},
    }

    resolved = resolve_code_runtime_context(manifest, TEST_DATABASE_URL, {}, store)

    assert resolved["artifact_uploads"]["out.json"]["storage_key"] == (
        "jobs-staging/ws-1/job-1/exec-1/out.json"
    )
    # 持久化的原 manifest 不被改写（内存态 copy）。
    assert "artifact_uploads" not in manifest


def test_inject_presign_expiry_follows_code_node_timeout(tmp_path: Path) -> None:
    """长超时节点：presign TTL = max(3600, timeout + 900)，PUT/GET 同值。"""
    _make_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    source = tmp_path / "q.json"
    source.write_bytes(PAYLOAD)
    store.upload(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        local_path=source,
    )
    manifest = _manifest()
    manifest["timeout_seconds"] = 7200  # kind='code' manifest 顶层键

    inject_artifact_object_block(store, manifest)

    assert storage.put_expiries == [8100]
    assert storage.get_expiries == [8100]


def test_inject_presign_expiry_follows_agent_execution_timeout(tmp_path: Path) -> None:
    """agent manifest 的 timeout 嵌在 execution 块下。"""
    _make_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    source = tmp_path / "q.json"
    source.write_bytes(PAYLOAD)
    store.upload(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        local_path=source,
    )
    manifest = _manifest()
    manifest["execution"] = {"provider": "p", "model": "m", "timeout_seconds": 10000}

    inject_artifact_object_block(store, manifest)

    assert storage.put_expiries == [10900]
    assert storage.get_expiries == [10900]


def test_inject_presign_expiry_defaults_to_one_hour() -> None:
    """manifest 无 timeout：TTL 落回 3600 下限（短 timeout 也被下限兜住）。"""
    _make_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    inject_artifact_object_block(store, _manifest())
    assert storage.put_expiries == [3600]

    short = _manifest()
    short["timeout_seconds"] = 60
    inject_artifact_object_block(store, short)
    assert storage.put_expiries == [3600, 3600]
