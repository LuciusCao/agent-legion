"""Agent claim 时内存态注入对象存储产物通道（#160 D12）。

enqueue 持久化的 manifest 只有 CAS 形态；Worker claim 到 agent 执行时，
claim 路由在下发副本上注入 artifact_uploads（presigned PUT）并把有
job_artifacts 行的 input_artifacts 升级为 {"url", "sha256"}——URL 不落
库、不在队列里过期。S3 异常降级为不注入，claim 本身不能挂。
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.routes.agent_worker_claims import create_agent_worker_claim_router
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b"claim-input-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()

FakeStorage = FakeObjectStorage


class RaisingStorage(FakeStorage):
    """presign_put 抛错：模拟 S3 不可达（claim 降级、不注入）。"""

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        raise RuntimeError("s3 unreachable")


def _seed() -> None:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-1', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, "
            " title, status, storage_dir) values ('job-1', 'ws-1', 's', 's1', 't', 'pending', 'd')"
        )


def _manifest() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "workspace_id": "ws-1",
        "execution_id": "exec-1",
        "expected_outputs": ["out.json"],
        "input_artifacts": {"q.json": f"sha256:{HASH}"},
        "execution": {"provider": "p", "model": "m"},
    }


def _claimed(manifest: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        execution_id="exec-1",
        lease_id="lease-1",
        workspace_id="ws-1",
        job_id="job-1",
        workflow_key="wf",
        node_key="node_a",
        agent_id="agent-1",
        kind="agent",
        manifest=manifest,
    )


def _client(object_store: JobArtifactObjectStore | None) -> tuple[TestClient, MagicMock]:
    broker = MagicMock()
    broker.claim.return_value = _claimed(_manifest())
    app = FastAPI()
    app.include_router(
        create_agent_worker_claim_router(
            broker,
            MagicMock(),
            lambda request, worker_id=None: {"worker_id": "w1", "protocol_version": 3},
            lambda request: "lease-1",
            object_store,
        )
    )
    return TestClient(app), broker


def _claim(client: TestClient) -> dict[str, Any]:
    response = client.post("/agent-executions/claim", json={"worker_id": "w1"})
    assert response.status_code == 200, response.text
    return response.json()["manifest"]


def test_agent_claim_injects_object_channel() -> None:
    _seed()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    # 上游产物已有 job_artifacts 行 → input 升级为 presigned GET。
    storage.objects["jobs/ws-1/job-1/q.json"] = PAYLOAD
    store.record_remote(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        storage_key="jobs/ws-1/job-1/q.json",
        size_bytes=len(PAYLOAD),
        content_hash=hashlib.sha256(PAYLOAD).hexdigest(),
    )
    client, _ = _client(store)

    manifest = _claim(client)

    uploads = manifest["artifact_uploads"]
    assert uploads["out.json"]["storage_key"] == "jobs-staging/ws-1/job-1/exec-1/out.json"
    assert uploads["out.json"]["url"].endswith("jobs-staging/ws-1/job-1/exec-1/out.json")
    assert uploads["out.json"]["url"].startswith("https://s3.test/upload/")
    ref = manifest["input_artifacts"]["q.json"]
    assert ref["url"].endswith("jobs/ws-1/job-1/q.json")
    assert ref["url"].startswith("https://s3.test/download/")
    assert ref["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert "storage_key" not in ref


def test_agent_claim_degrades_on_storage_error() -> None:
    _seed()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, RaisingStorage())
    client, _ = _client(store)

    manifest = _claim(client)  # claim 不挂，正常 200

    assert "artifact_uploads" not in manifest
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}


def test_agent_claim_without_object_storage_unchanged() -> None:
    _seed()
    client, _ = _client(None)

    manifest = _claim(client)

    assert "artifact_uploads" not in manifest
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}


def test_agent_claim_v4_worker_gets_gzip_specs() -> None:
    """#338：v4 worker（authorize 报 protocol_version=4）拿 .gz 上传 spec，
    .gz 输入行升级为 presigned GET + content_encoding 标记。"""
    import gzip as gzip_mod

    _seed()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    compressed = gzip_mod.compress(PAYLOAD)
    storage.objects["jobs/ws-1/job-1/q.json.gz"] = compressed
    store.record_remote(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        storage_key="jobs/ws-1/job-1/q.json.gz",
        size_bytes=len(compressed),
        content_hash=HASH,
    )
    broker = MagicMock()
    broker.claim.return_value = _claimed(_manifest())
    app = FastAPI()
    app.include_router(
        create_agent_worker_claim_router(
            broker,
            MagicMock(),
            lambda request, worker_id=None: {"worker_id": "w1", "protocol_version": 4},
            lambda request: "lease-1",
            store,
        )
    )
    client = TestClient(app)

    manifest = _claim(client)

    assert manifest["artifact_uploads"]["out.json"]["storage_key"] == (
        "jobs-staging/ws-1/job-1/exec-1/out.json.gz"
    )
    ref = manifest["input_artifacts"]["q.json"]
    assert ref["url"].endswith("jobs/ws-1/job-1/q.json.gz")
    assert ref["sha256"] == HASH  # 未压缩字节哈希
    assert ref["content_encoding"] == "gzip"


def test_agent_claim_mixed_fleet_v3_worker_keeps_raw_specs() -> None:
    """#338 混合舰队：v3 worker 对同一批 .gz 数据拿裸上传 spec，.gz 输入行
    不升级（保留 CAS 形态）——不因输入/上传形态 mismatch 失败。"""
    import gzip as gzip_mod

    _seed()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    compressed = gzip_mod.compress(PAYLOAD)
    storage.objects["jobs/ws-1/job-1/q.json.gz"] = compressed
    store.record_remote(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        storage_key="jobs/ws-1/job-1/q.json.gz",
        size_bytes=len(compressed),
        content_hash=HASH,
    )
    client, _ = _client(store)  # _client 的 authorize stub 报 protocol_version=3

    manifest = _claim(client)

    assert manifest["artifact_uploads"]["out.json"]["storage_key"] == (
        "jobs-staging/ws-1/job-1/exec-1/out.json"
    )
    assert manifest["input_artifacts"] == {"q.json": f"sha256:{HASH}"}
    assert storage.presigned_gets == []  # 未为旧 worker 签发 .gz GET


def test_agent_claim_presign_expiry_follows_execution_timeout() -> None:
    """presign TTL 从 execution.timeout_seconds 派生：max(3600, t + 900)。"""
    _seed()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    storage.objects["jobs/ws-1/job-1/q.json"] = PAYLOAD
    store.record_remote(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="upstream",
        name="q.json",
        storage_key="jobs/ws-1/job-1/q.json",
        size_bytes=len(PAYLOAD),
        content_hash=hashlib.sha256(PAYLOAD).hexdigest(),
    )
    broker = MagicMock()
    long_timeout = _manifest()
    long_timeout["execution"]["timeout_seconds"] = 7200
    broker.claim.side_effect = [
        _claimed(long_timeout),  # 长 timeout → TTL 拉长
        _claimed(_manifest()),  # 无 timeout → 3600 下限
    ]
    app = FastAPI()
    app.include_router(
        create_agent_worker_claim_router(
            broker,
            MagicMock(),
            lambda request, worker_id=None: {"worker_id": "w1", "protocol_version": 3},
            lambda request: "lease-1",
            store,
        )
    )
    client = TestClient(app)

    _claim(client)
    _claim(client)

    assert storage.put_expiries == [8100, 3600]
    assert storage.get_expiries == [8100, 3600]
