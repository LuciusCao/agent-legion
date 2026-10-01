"""Upload-to-reclaim handoff invariants for one local execution directory."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.artifact.upload import DirectUploadError
from worker.execution import run as execution_run
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


def test_incoming_lease_loss_interrupts_prior_upload_wait(tmp_path: Path) -> None:
    """等待旧 uploader 时新 lease 判死，应立即放弃而非等传输超时。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = _GatedClient()
    queue = _queue(client)
    task = _task(work_root)
    queue.submit(task)
    assert client.upload_entered.wait(10)

    incoming_lost = threading.Event()
    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(
            queue.wait_for_prior_upload("exec-1", "lease-new", threading.Event(), incoming_lost)
        )
    )
    waiter.start()
    assert task.ownership_lost.wait(10)
    incoming_lost.set()
    waiter.join(timeout=2)

    assert not waiter.is_alive()
    assert result == [False]
    assert queue.depth == 1, "incoming claim must not finalize the prior task"

    client.release_upload.set()
    queue.shutdown()
    assert queue.depth == 0


def test_post_handoff_failure_prunes_incoming_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """handoff 后本地异常不得留下永续租的未启动 claim。"""
    heartbeats = []
    real_start = execution_run.start_lease_heartbeat

    def capture_heartbeat(*args: object, **kwargs: object):
        heartbeat = real_start(*args, **kwargs)  # type: ignore[arg-type]
        heartbeats.append(heartbeat)
        return heartbeat

    monkeypatch.setattr(execution_run, "start_lease_heartbeat", capture_heartbeat)

    class ReadyUploads:
        def wait_for_prior_upload(self, *args: object) -> bool:
            return True

    class FailingStatus:
        def start(self, execution_id: str, **fields: object) -> None:
            raise RuntimeError("status failed")

    with pytest.raises(RuntimeError, match="status failed"):
        execution_run.run_execution(
            object(),
            {"execution_id": "exec-1", "lease_id": "lease-new", "node_key": "node_a"},
            tmp_path / "work",
            {},
            0.05,
            threading.Event(),
            1,
            FailingStatus(),  # type: ignore[arg-type]
            ReadyUploads(),  # type: ignore[arg-type]
            threading.Semaphore(1),
        )

    assert heartbeats and heartbeats[0].stop.is_set()


def test_agent_lost_during_prepare_is_not_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """准备阶段判死的 agent claim 不得获得任何子进程副作用。"""
    ownership_events: list[threading.Event] = []
    heartbeats = []
    real_start = execution_run.start_lease_heartbeat

    def capture_heartbeat(*args: object, **kwargs: object):
        ownership_events.append(args[4])  # type: ignore[arg-type]
        heartbeat = real_start(*args, **kwargs)  # type: ignore[arg-type]
        heartbeats.append(heartbeat)
        return heartbeat

    def lose_during_prepare(
        client: object,
        claim: dict,
        execution_dir: Path,
        download_slots: threading.Semaphore,
    ) -> SimpleNamespace:
        run_dir = execution_dir / "job" / "runs" / "node_a" / "worker"
        run_dir.mkdir(parents=True)
        ownership_events[0].set()
        return SimpleNamespace(
            manifest={"execution": {"timeout_seconds": 1}, "expected_outputs": []},
            command=["true"],
        )

    spawned = 0
    real_popen = execution_run.subprocess.Popen

    def counting_popen(*args: object, **kwargs: object):
        nonlocal spawned
        spawned += 1
        return real_popen(*args, **kwargs)  # type: ignore[call-overload]

    class ReadyUploads:
        submitted = False

        def wait_for_prior_upload(self, *args: object) -> bool:
            return True

        def submit(self, task: object) -> None:
            self.submitted = True

    monkeypatch.setattr(execution_run, "start_lease_heartbeat", capture_heartbeat)
    monkeypatch.setattr(execution_run, "prepare_execution", lose_during_prepare)
    monkeypatch.setattr(execution_run.subprocess, "Popen", counting_popen)
    uploads = ReadyUploads()
    execution_run.run_execution(
        object(),
        {
            "execution_id": "exec-1",
            "lease_id": "lease-new",
            "node_key": "node_a",
            "bundle_url": "/bundle",
        },
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        ExecutionStatusReporter(None),
        uploads,  # type: ignore[arg-type]
        threading.Semaphore(1),
    )

    assert spawned == 0
    assert not uploads.submitted
    assert heartbeats and heartbeats[0].stop.is_set()
