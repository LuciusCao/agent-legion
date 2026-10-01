"""Unit tests for the Worker upload queue (worker/upload/queue.py)."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from worker.execution.heartbeat_batch import BatchHeartbeatRegistry
from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop
from worker.execution.ownership import OWNER_FILENAME, write_owner_marker
from worker.status import ExecutionStatusReporter
from worker.upload import queue as upload_queue
from worker.upload.queue import PENDING_FILENAME, UploadQueue, UploadTask


class QueueFakeClient:
    def __init__(self, report_status: int = 204) -> None:
        self.reports: list[dict] = []
        self.uploads: dict[str, bytes] = {}
        self.heartbeats = 0
        self.report_status = report_status
        self.report_errors = 0
        self.heartbeats_at_report: list[int] = []

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.heartbeats_at_report.append(self.heartbeats)
        if self.report_errors > 0:
            self.report_errors -= 1
            raise RuntimeError("download failed: /x: timed out")
        self.reports.append(metadata)
        return self.report_status, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        return 204, []

    def heartbeat_batch(self, leases: list[tuple[str, str]]) -> tuple[int, dict]:
        return 200, {
            "renewed": [execution_id for execution_id, _ in leases],
            "lost": [],
            "cancelled_execution_ids": [],
        }


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
            "workspace_id": "ws-1",
            "agent_id": "agent",
            "run_dir": "run",
        },
        "kind": kind,
    }
    if kind == "process":
        defaults.update({"exit_code": 0, "expected_outputs": ("output.json",), "command": ("pi",)})
    defaults.update(kwargs)
    return UploadTask(**defaults)


def _queue(
    client: QueueFakeClient,
    stop: threading.Event | None = None,
    registry: BatchHeartbeatRegistry | None = None,
) -> UploadQueue:
    # #644：registry 不传 = legacy 单拍模式。registry 模式的用例必须经
    # heartbeat_registry 接线进 queue——_deliver_bulk 会用 queue 的 registry
    # 覆盖 task 的（executor.py 的生产接线同形），只在 task 上预置 registry
    # 测不到 registry 车道（退避 resume 用例曾因此空转）。
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=stop or threading.Event(),
        heartbeat_registry=registry,
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
    """409 判定后 marker 必删（不重试、重启不重投）；无 owner 标记时目录
    归 stale sweeper（#644 收尾语义，归属钉子见下方姊妹用例）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient(report_status=409)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1  # a verdict, not a transient error
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()
    assert (work_root / "exec-1").is_dir()


def test_report_409_discards_via_ownership_marker(tmp_path: Path) -> None:
    """#644：409 后的目录收尾必须走 #564 归属检查——owner 标记仍指本 lease
    才 rmtree；已被重排的新 attempt 以新 lease 重建占用时只删 marker、
    不删目录（证明不了归属的目录归 stale sweeper）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    write_owner_marker(work_root / "exec-1", {"execution_id": "exec-1", "lease_id": "lease-1"})
    client = QueueFakeClient(report_status=409)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1
    # owner marker matches the task's lease → whole dir discarded.
    assert not (work_root / "exec-1").exists()


def test_report_409_spares_reclaimed_execution_dir(tmp_path: Path) -> None:
    """#644：409 说明执行可能已被 Host 重排——若目录已被新 attempt（新 lease）
    重建占用，丢弃收尾不得 rmtree 它；marker 必须删掉（结果已 moot，重启
    restore 不得重投）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    write_owner_marker(work_root / "exec-1", {"execution_id": "exec-1", "lease_id": "lease-new"})
    client = QueueFakeClient(report_status=409)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()
    # The re-claimed attempt's dir survives; only the moot result is dropped.
    assert (work_root / "exec-1").is_dir()
    assert (work_root / "exec-1" / OWNER_FILENAME).is_file()


def test_report_409_without_owner_marker_spares_dir(tmp_path: Path) -> None:
    """无 owner 标记（恢复任务/旧目录形态）= 无法证明归属：只删 marker，
    目录留给 stale sweeper（#564 的正确性底线：never rmtree what you
    cannot prove is yours）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient(report_status=409)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()
    assert (work_root / "exec-1").is_dir()


def test_ownership_lost_abandons_report_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#644 核心：退避等待中心跳面判死（beat 409/lost verdict）→ 下一轮
    report 不再发出（同 execution 不再连打），终态放弃，marker 删除、
    目录按归属收尾。"""
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.05)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    client.report_errors = 2  # 两次瞬时失败（退避窗口内判死）
    task = _task(work_root)
    task.ownership_lost = threading.Event()

    original_report = client.report

    def lost_after_first_failure(*args: object) -> tuple[int, bytes]:
        task.ownership_lost.set()  # verdict lands during the backoff window
        return original_report(*args)  # type: ignore[arg-type]

    client.report = lost_after_first_failure  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    # First attempt raised (transient); the loop then abandoned before
    # sending attempt #2 — no same-execution 409 hammering.
    assert len(client.reports) == 0
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()
    # 判不了归属（无 owner 标记）→ 目录保留给 stale sweeper，不整删。
    assert (work_root / "exec-1").is_dir()


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


def test_heartbeat_quiesced_before_report(tmp_path: Path) -> None:
    """The report is the last proof of life: no beat may race its commit,
    or the loser's 409 logs a spurious "lost ownership"."""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root)
    # Simulate the live heartbeat handed over by the execution thread.
    beat = threading.Thread(
        target=heartbeat_loop,
        args=(
            HeartbeatConfig(
                client=client,
                execution_id=task.execution_id,
                lease_id=task.lease_id,
                stop=task.heartbeat_stop,
                interval=0.05,
                ownership_lost=threading.Event(),
                proc_ref={"proc": None},
                adopted=threading.Event(),
            ),
        ),
        daemon=True,
    )
    task.heartbeat_thread = beat
    beat.start()
    observed: dict[str, bool] = {}
    real_report = client.report

    def report(*args: object) -> tuple[int, bytes]:
        observed["stop_set"] = task.heartbeat_stop.is_set()
        observed["beat_alive"] = beat.is_alive()
        return real_report(*args)  # type: ignore[arg-type]

    client.report = report  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()
    assert observed == {"stop_set": True, "beat_alive": False}


def test_heartbeat_resumes_during_report_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient report failure must re-arm the lease heartbeat for the
    backoff window — an unbounded backoff chain can outlive the lease TTL."""
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.2)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    client.report_errors = 1
    queue = _queue(client)  # heartbeat interval 0.05s << 0.2s backoff
    queue.submit(_task(work_root))
    queue.shutdown()
    assert len(client.reports) == 1
    first_attempt, second_attempt = client.heartbeats_at_report
    assert second_attempt > first_attempt  # beats resumed during the backoff


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


def test_submit_serialization_failure_unwinds_handoff(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    queue = _queue(QueueFakeClient())

    with pytest.raises(TypeError):
        queue.submit(_task(work_root, status_fields={"not_json": object()}))

    assert queue.depth == 0
    queue.shutdown()


def test_condemned_cleanup_failure_still_releases_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root)
    task.ownership_lost.set()
    queue = _queue(QueueFakeClient())

    def fail_cleanup(_task: UploadTask) -> bool:
        raise OSError("cleanup failed")

    monkeypatch.setattr(upload_queue, "drop_marker", fail_cleanup)
    queue.submit(task)
    queue.shutdown()

    assert queue.depth == 0
    assert task.delivery_done.is_set()


class BlockingReportClient(QueueFakeClient):
    """Report call parks on a gate so queue depth can be observed deterministically."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.entered.set()
        assert self.release.wait(10)
        return super().report(execution_id, lease_id, metadata, archive)


def _restore_two_pending(work_root: Path) -> None:
    for execution_id in ("exec-1", "exec-2"):
        execution_dir = _execution_dir(work_root, execution_id)
        task = _task(work_root, execution_id=execution_id)
        marker = execution_dir / PENDING_FILENAME
        marker.write_text(json.dumps(task.to_json()), encoding="utf-8")


def test_restore_backlog_visible_as_queued_upload(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _restore_two_pending(work_root)
    client = BlockingReportClient()
    status_path = tmp_path / "status.json"
    queue = UploadQueue(
        client,
        ExecutionStatusReporter(status_path),
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    assert queue.restore(work_root) == 2
    assert client.entered.wait(10)
    try:
        # exec-1 占住唯一上传线程；exec-2 积压在池外，也必须以 queued_upload 可见。
        executions = json.loads(status_path.read_text(encoding="utf-8"))["executions"]
        assert executions["exec-1"]["phase"] == "uploading"
        assert executions["exec-2"]["phase"] == "queued_upload"
        assert executions["exec-2"]["node_key"] == "node_a"
    finally:
        client.release.set()
        queue.shutdown()
    assert len(client.reports) == 2


def test_submit_existing_entry_only_updates_phase(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    status_path = tmp_path / "status.json"
    reporter = ExecutionStatusReporter(status_path)
    reporter.start("exec-1", node_key="node_a")
    original = json.loads(status_path.read_text(encoding="utf-8"))["executions"]["exec-1"]
    client = BlockingReportClient()
    queue = UploadQueue(
        client,
        reporter,
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    queue.submit(_task(work_root))
    assert client.entered.wait(10)
    try:
        entry = json.loads(status_path.read_text(encoding="utf-8"))["executions"]["exec-1"]
        assert entry["phase"] == "uploading"
        assert entry["started_at"] == original["started_at"]
    finally:
        client.release.set()
        queue.shutdown()
    assert len(client.reports) == 1
