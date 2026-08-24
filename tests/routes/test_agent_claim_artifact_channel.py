"""Agent claim 时内存态注入对象存储产物通道（#160 D12）。

enqueue 持久化的 manifest 只有 CAS 形态；Worker claim 到 agent 执行时，
claim 路由在下发副本上注入 artifact_uploads（presigned PUT）并把有
job_artifacts 行的 input_artifacts 升级为 {"url", "sha256"}——URL 不落
库、不在队列里过期。S3 异常降级为不注入，claim 本身不能挂。
"""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace
from typing import Any, BinaryIO
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.routes.agent_worker_claims import create_agent_worker_claim_router
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectHead
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b"claim-input-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}?sig=put"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
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


def _seed() -> None:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-1', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values ('job-1', 'ws-1', 'wf', 's', 's1', 't', 'pending', 'd')"
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
    assert uploads["out.json"]["url"].endswith("?sig=put")
    ref = manifest["input_artifacts"]["q.json"]
    assert ref["url"].endswith("?sig=get")
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
