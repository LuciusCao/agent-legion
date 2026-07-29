"""Unit tests for the Worker upload queue (worker/upload_queue.py)."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from worker import upload_queue
from worker.status import ExecutionStatusReporter
from worker.upload_queue import PENDING_FILENAME, UploadQueue, UploadTask


class QueueFakeClient:
    def __init__(self, report_status: int = 204) -> None:
        self.reports: list[dict] = []
        self.uploads: dict[str, bytes] = {}
        self.heartbeats = 0
        self.report_status = report_status
        self.report_errors = 0

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        if self.report_errors > 0:
            self.report_errors -= 1
            raise RuntimeError("download failed: /x: timed out")
        self.reports.append(metadata)
        return self.report_status, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> int:
        self.heartbeats += 1
        return 204


def _execution_dir(work_root: Path, execution_id: str = "exec-1") -> Path:
    run_dir = work_root / execution_id / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (work_root / execution_id / "job" / "output.json").write_text("{}", encoding="utf-8")
    return work_root / execution_id


def _task(
    work_root: Path, kind: str = "process", execution_id: str = "exec-1", **kwargs
) -> UploadTask:
    defaults: dict = {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "execution_dir": work_root / execution_id,
        "node_key": "node_a",
        "status_fields": {
            "job_id": "job-1",
            "node_key": "node_a",
            "workflow_key": "wf",
            "agent_id": "agent",
            "run_dir": "run",
        },
        "kind": kind,
    }
    if kind == "process":
        defaults.update({"exit_code": 0, "expected_outputs": ("output.json",), "command": ("pi",)})
    defaults.update(kwargs)
    return UploadTask(**defaults)


def _queue(client: QueueFakeClient, stop: threading.Event | None = None) -> UploadQueue:
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=stop or threading.Event(),
    )


def test_process_task_delivers_and_cleans_up(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["exit_code"] == 0
    assert "output.json" in report["output_artifacts"]
    # Delivery removes the marker and the whole execution dir.
    assert not (work_root / "exec-1").exists()


def test_prebuilt_task_reports_metadata_without_artifacts(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(
        _task(
            work_root,
            kind="prebuilt",
            prebuilt_metadata={
                "status": "failed",
                "exit_code": 1,
                "error_message": "download failed: /bundle: timed out",
            },
        )
    )
    queue.shutdown()
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "failed"
    assert "timed out" in client.reports[0]["error_message"]
    assert client.uploads == {}
    assert not (work_root / "exec-1").exists()


def test_report_lease_conflict_discards_without_retry(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient(report_status=409)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1  # a verdict, not a transient error
    assert not (work_root / "exec-1").exists()


def test_report_transient_error_retries_until_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.01)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    client.report_errors = 2
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1
    assert not (work_root / "exec-1").exists()


def test_restore_requeues_pending_markers(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(
        work_root,
        kind="prebuilt",
        prebuilt_metadata={"status": "failed", "exit_code": 1, "error_message": "x"},
    )
    marker = work_root / "exec-1" / PENDING_FILENAME
    marker.write_text(json.dumps(task.to_json()), encoding="utf-8")
    client = QueueFakeClient()
    queue = _queue(client)
    assert queue.restore(work_root) == 1
    queue.shutdown()
    assert len(client.reports) == 1
    assert not (work_root / "exec-1").exists()


def test_restore_discards_unreadable_marker(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    (work_root / "exec-1" / PENDING_FILENAME).write_text("not json", encoding="utf-8")
    client = QueueFakeClient()
    queue = _queue(client)
    assert queue.restore(work_root) == 0
    queue.shutdown()
    assert client.reports == []
    assert not (work_root / "exec-1").exists()


def test_stopped_queue_keeps_marker_for_next_startup(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    stop = threading.Event()
    stop.set()
    queue = _queue(client, stop=stop)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert client.reports == []
    assert (work_root / "exec-1" / PENDING_FILENAME).is_file()


def test_depth_gauge_tracks_queued_work(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    stop = threading.Event()
    stop.set()  # tasks never deliver; depth stays until shutdown drains
    queue = _queue(client, stop=stop)
    queue.submit(_task(work_root))
    queue.shutdown()  # drains (tasks bail out immediately on stop)
    assert queue.depth == 0
