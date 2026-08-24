"""Worker 产物对象存储通道（#160 D12）：presigned PUT 直传、tar 不内嵌、
input_artifacts dict 形态下载。

与 Host 侧 tests/services/test_agent_completion_remote.py、
tests/services/test_agent_artifact_inject.py 互为两端。
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from worker import artifact_download, artifact_upload
from worker.artifact_upload import DirectUploadError, upload_artifact_direct
from worker.bundle_io import download_input_artifacts
from worker.code_runner import prepare_code_result
from worker.status import ExecutionStatusReporter
from worker.upload_queue import UploadQueue, UploadTask

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


def test_upload_artifact_direct_rejects_incomplete_spec(tmp_path: Path) -> None:
    path = tmp_path / "output.json"
    path.write_bytes(PAYLOAD)
    with pytest.raises(DirectUploadError, match="incomplete"):
        upload_artifact_direct(path, {"storage_key": "jobs/ws/job-1/output.json"})


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


class _DownloadFakeClient:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs
        self.requests: list[str] = []

    def download(self, path: str, destination: Path) -> None:
        self.requests.append(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._blobs[path])


def _fake_open_download(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    urls: list[str] = []

    def _open(url: str) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(artifact_download, "_open_download", _open)
    return urls


def test_download_input_artifacts_dict_form_uses_presigned_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _fake_open_download(monkeypatch, PAYLOAD)
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert urls == ["https://s3.test/get/x?sig=1"]
    assert client.requests == []  # 旧 CAS 通道未被调用
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_dict_form_verifies_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_open_download(monkeypatch, b"tampered")
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    with pytest.raises(RuntimeError, match="digest mismatch"):
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]


def test_download_input_artifacts_string_form_keeps_cas_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _fake_open_download(monkeypatch, b"unused")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {"input_artifacts": {"inputs/q.json": f"sha256:{HASH}"}}

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert urls == []
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD
