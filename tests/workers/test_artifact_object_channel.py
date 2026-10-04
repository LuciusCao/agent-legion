"""Worker 产物对象存储通道（#160 D12）：presigned PUT 直传、tar 不内嵌。

input_artifacts dict 形态下载族（digest 自验 + presigned 失配/失败的
CAS 回落，#876）在 tests/workers/test_artifact_input_downloads.py
（文件体积纪律拆分）。

与 Host 侧 tests/services/test_agent_completion_remote.py、
tests/services/test_agent_artifact_inject.py 互为两端。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
import threading
from pathlib import Path
from typing import Any, BinaryIO

import pytest
import requests

from worker.artifact import upload as artifact_upload
from worker.artifact.upload import DirectUploadError, upload_artifact_direct
from worker.result_archive import prepare_code_result
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue, UploadTask

pytestmark = pytest.mark.no_db

PAYLOAD = b"artifact-bytes" * 100
HASH = hashlib.sha256(PAYLOAD).hexdigest()
SPEC = {
    "storage_key": "jobs-staging/ws-1/job-1/exec-1/output.json",
    "url": "https://s3.test/put/x?sig=1",
}


def _fake_put(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[bytes]:
    """替换 artifact_upload._put_stream；返回每次收到的字节流。"""
    received: list[bytes] = []
    remaining = list(statuses)

    def _put(url: str, stream: BinaryIO, size_bytes: int) -> int:
        received.append(stream.read())
        return remaining.pop(0)

    monkeypatch.setattr(artifact_upload, "_put_stream", _put)
    return received


def test_upload_artifact_direct_streams_and_reports_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    received = _fake_put(monkeypatch, [200])

    ref = upload_artifact_direct(path, SPEC)

    assert received == [PAYLOAD]
    assert ref == {
        "storage_key": SPEC["storage_key"],
        "size_bytes": len(PAYLOAD),
        "content_hash": HASH,
    }


def test_upload_artifact_direct_gzip_spec_compresses_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#338：spec storage_key 带 .gz 后缀 → PUT 压缩字节；ref 的 size_bytes
    是压缩后（HEAD 可核验的唯一数字），content_hash 仍是未压缩字节哈希。"""
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    received = _fake_put(monkeypatch, [200])
    spec = {**SPEC, "storage_key": SPEC["storage_key"] + ".gz"}

    ref = upload_artifact_direct(path, spec)

    assert len(received) == 1
    assert gzip.decompress(received[0]) == PAYLOAD  # PUT 的是 gzip 流
    assert received[0] != PAYLOAD and len(received[0]) < len(PAYLOAD)
    assert ref == {
        "storage_key": spec["storage_key"],
        "size_bytes": len(received[0]),  # 压缩后字节数
        "content_hash": HASH,  # 未压缩字节哈希，语义不变
    }


def test_upload_artifact_direct_gzip_retries_reuse_compressed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gzip 形态重试不再读盘：每次 attempt 复用同一份压缩字节。"""
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    monkeypatch.setattr(artifact_upload, "_RETRY_BASE_SECONDS", 0.01)
    received = _fake_put(monkeypatch, [500, 200])
    spec = {**SPEC, "storage_key": SPEC["storage_key"] + ".gz"}

    ref = upload_artifact_direct(path, spec)

    assert len(received) == 2
    assert received[0] == received[1]
    assert ref is not None and ref["content_hash"] == HASH


def test_upload_artifact_direct_4xx_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    received = _fake_put(monkeypatch, [403, 200])

    with pytest.raises(DirectUploadError, match="HTTP 403"):
        upload_artifact_direct(path, SPEC)
    assert len(received) == 1  # 终态 verdict 不重试


def test_upload_artifact_direct_retries_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    monkeypatch.setattr(artifact_upload, "_RETRY_BASE_SECONDS", 0.01)
    received = _fake_put(monkeypatch, [500, 502, 200])

    ref = upload_artifact_direct(path, SPEC)

    assert len(received) == 3  # 每次重试重新打开流
    assert ref is not None and ref["content_hash"] == HASH


def test_upload_artifact_direct_does_not_reopen_after_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    stop = threading.Event()
    received: list[bytes] = []

    def _put(url: str, stream: BinaryIO, size_bytes: int) -> int:
        received.append(stream.read())
        stop.set()
        return 500

    monkeypatch.setattr(artifact_upload, "_put_stream", _put)
    assert upload_artifact_direct(path, SPEC, stop=stop) is None
    assert received == [PAYLOAD]


def test_upload_artifact_direct_rejects_incomplete_spec(tmp_path: Path) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    with pytest.raises(DirectUploadError, match="incomplete"):
        upload_artifact_direct(path, {"storage_key": "jobs/ws/job-1/output.json"})


def test_upload_artifact_direct_rejects_non_mapping_spec(tmp_path: Path) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    with pytest.raises(DirectUploadError, match="unexpected type"):
        upload_artifact_direct(path, "not-a-mapping")  # type: ignore[arg-type]


def test_upload_artifact_direct_error_hides_presigned_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """requests 网络异常的 str(exc) 含完整签名 URL；落库前必须只留类型名。"""
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    monkeypatch.setattr(artifact_upload, "_RETRY_BASE_SECONDS", 0.01)

    def _put(url: str, stream: BinaryIO, size_bytes: int) -> int:
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='s3.test', port=443): Max retries exceeded"
            " with url: /put/x?X-Amz-Credential=AKID&X-Amz-Signature=abc123"
        )

    monkeypatch.setattr(artifact_upload, "_put_stream", _put)
    with pytest.raises(DirectUploadError) as excinfo:
        upload_artifact_direct(path, dict(SPEC))
    message = str(excinfo.value)
    assert "ConnectionError" in message
    assert "X-Amz-Signature" not in message
    assert "X-Amz-Credential" not in message
    assert "s3.test" not in message


class QueueFakeClient:
    def __init__(self) -> None:
        self.reports: list[dict] = []
        self.uploads: dict[str, bytes] = {}

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        self._archive = archive.read_bytes()
        return 204, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        return 204, []


def _execution_dir(work_root: Path, execution_id: str = "exec-1") -> Path:
    run_dir = work_root / execution_id / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (work_root / execution_id / "job" / "output.json").write_bytes(PAYLOAD)
    return work_root / execution_id


def _task(work_root: Path, **kwargs: Any) -> UploadTask:
    defaults: dict[str, Any] = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "execution_dir": work_root / "exec-1",
        "node_key": "node_a",
        "status_fields": {"job_id": "job-1", "node_key": "node_a"},
        "kind": "process",
        "exit_code": 0,
        "expected_outputs": ("output.json",),
        "command": ("pi",),
    }
    defaults.update(kwargs)
    return UploadTask(**defaults)


def _queue(client: QueueFakeClient) -> UploadQueue:
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )


def _archive_members(client: QueueFakeClient, tmp_path: Path) -> set[str]:
    archive = tmp_path / "reported.tar.gz"
    archive.write_bytes(client._archive)
    with tarfile.open(archive, "r:gz") as tar:
        return {member.name for member in tar.getmembers()}


def test_queue_direct_upload_skips_tar_embed_and_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    received = _fake_put(monkeypatch, [200])
    client = QueueFakeClient()
    queue = _queue(client)

    queue.submit(_task(work_root, artifact_uploads={"output.json": dict(SPEC)}))
    queue.shutdown()

    assert received == [PAYLOAD]
    assert client.uploads == {}  # 旧 CAS 通道未被调用
    assert len(client.reports) == 1
    ref = client.reports[0]["output_artifacts"]["output.json"]
    assert ref == {
        "storage_key": SPEC["storage_key"],
        "size_bytes": len(PAYLOAD),
        "content_hash": HASH,
    }
    members = _archive_members(client, tmp_path)
    assert "output.json" not in members  # 直传通道 tar 不内嵌产物
    assert "runs/node_a/worker/events.jsonl" in members


def test_queue_without_upload_spec_keeps_legacy_channel(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    queue = _queue(client)

    queue.submit(_task(work_root))
    queue.shutdown()

    assert len(client.uploads) == 1  # CAS POST 通道
    ref = client.reports[0]["output_artifacts"]["output.json"]
    assert ref == f"sha256:{HASH}"
    members = _archive_members(client, tmp_path)
    assert "output.json" in members  # 旧通道 tar 内嵌产物


def test_queue_direct_upload_failure_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直传 4xx（DirectUploadError）不判 run failed：清规格重跑 prepare，
    tar 内嵌产物，整体走 CAS 通道上报 completed。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _fake_put(monkeypatch, [403])  # 终态 verdict → DirectUploadError
    client = QueueFakeClient()
    queue = _queue(client)

    queue.submit(_task(work_root, artifact_uploads={"output.json": dict(SPEC)}))
    queue.shutdown()

    assert len(client.uploads) == 1  # 回落 CAS POST 通道
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"] == f"sha256:{HASH}"
    members = _archive_members(client, tmp_path)
    assert "output.json" in members  # 回落后 tar 内嵌产物


def test_queue_malformed_upload_spec_falls_back_to_cas(tmp_path: Path) -> None:
    """非 Mapping 的畸形 spec 归一为 DirectUploadError，同样回落 CAS。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    queue = _queue(client)

    queue.submit(_task(work_root, artifact_uploads={"output.json": "not-a-mapping"}))
    queue.shutdown()

    assert len(client.uploads) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"] == f"sha256:{HASH}"
    members = _archive_members(client, tmp_path)
    assert "output.json" in members


def test_prepare_code_result_skips_outputs_on_direct_channel(tmp_path: Path) -> None:
    execution_dir = tmp_path / "exec-1"
    job_dir = execution_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "output.json").write_bytes(PAYLOAD)
    (execution_dir / "node.log").write_text("log", encoding="utf-8")
    base: dict[str, Any] = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "execution_dir": execution_dir,
        "node_key": "node_a",
        "status_fields": {},
        "kind": "process",
        "exec_kind": "code",
        "exit_code": 0,
        "expected_outputs": ("output.json",),
        "code_result": {"status": "completed", "error_message": ""},
    }

    _, archive, _ = prepare_code_result(
        UploadTask(**base, artifact_uploads={"output.json": dict(SPEC)})
    )
    with tarfile.open(archive, "r:gz") as tar:
        members = {member.name for member in tar.getmembers()}
    assert members == {"node.log"}

    _, archive, _ = prepare_code_result(UploadTask(**base))
    with tarfile.open(archive, "r:gz") as tar:
        members = {member.name for member in tar.getmembers()}
    assert members == {"node.log", "output.json"}
