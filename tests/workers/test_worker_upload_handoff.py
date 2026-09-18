"""Upload-to-reclaim handoff invariants for one local execution directory."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from worker.artifact.upload import DirectUploadError
from worker.execution.ownership import write_owner_marker
from worker.status import ExecutionStatusReporter
from worker.upload import queue as upload_queue
from worker.upload.queue import UploadQueue, UploadTask


class _GatedClient:
    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}
        self.reports: list[dict] = []
        self.upload_entered = threading.Event()
        self.release_upload = threading.Event()

    def upload_artifact(self, path: Path) -> str:
        if not self.upload_entered.is_set():
            self.upload_entered.set()
            assert self.release_upload.wait(10)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        return 204, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        return 204, []


def _execution_dir(work_root: Path) -> Path:
    execution_dir = work_root / "exec-1"
    run_dir = execution_dir / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (execution_dir / "job" / "output.json").write_text("{}", encoding="utf-8")
    write_owner_marker(execution_dir, {"execution_id": "exec-1", "lease_id": "lease-1"})
    return execution_dir


def _task(work_root: Path, **kwargs: object) -> UploadTask:
    fields: dict[str, object] = {
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
    fields.update(kwargs)
    return UploadTask(**fields)  # type: ignore[arg-type]


def _queue(client: _GatedClient) -> UploadQueue:
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )


def test_direct_failure_does_not_prepare_fallback_after_lease_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct 内层失败同时收到 lost verdict 时，fallback prepare 不得再碰目录。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = _GatedClient()
    client.release_upload.set()
    queue = _queue(client)
    task = _task(
        work_root,
        artifact_uploads={"output.json": {"url": "https://x", "storage_key": "k"}},
    )
    prepare_calls = 0
    real_prepare = upload_queue.prepare_or_failed

    def counted_prepare(upload_task: UploadTask):
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(upload_task)

    def lose_then_fail(*args: object, **kwargs: object) -> dict | None:
        task.ownership_lost.set()
        raise DirectUploadError("HTTP 503")

    monkeypatch.setattr(upload_queue, "prepare_or_failed", counted_prepare)
    monkeypatch.setattr(upload_queue, "upload_artifact_direct", lose_then_fail)
    queue.submit(task)
    queue.shutdown()

    assert prepare_calls == 1, "lost task re-entered fallback prepare"
    assert client.uploads == {}
    assert client.reports == []
    assert queue.depth == 0


def test_new_attempt_waits_for_prior_upload_directory_teardown(tmp_path: Path) -> None:
    """新 lease 在旧 uploader 完成 marker/rmtree 前不得复用 execution_dir。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "job" / "second.json").write_text("{}", encoding="utf-8")
    client = _GatedClient()
    queue = _queue(client)
    task = _task(work_root, expected_outputs=("output.json", "second.json"))
    queue.submit(task)
    assert client.upload_entered.wait(10)

    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(
            queue.wait_for_prior_upload("exec-1", "lease-new", threading.Event())
        )
    )
    waiter.start()
    assert task.ownership_lost.wait(10), "new attempt did not condemn the prior uploader"
    assert waiter.is_alive(), "directory handoff completed before the in-flight upload stopped"

    client.release_upload.set()
    waiter.join(timeout=10)
    assert not waiter.is_alive()
    assert result == [True]
    assert queue.depth == 0
    assert not execution_dir.exists(), "handoff acknowledged before old-dir cleanup"
    queue.shutdown()
