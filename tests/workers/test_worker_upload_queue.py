"""Unit tests for the Worker upload queue (worker/upload/queue.py)."""

from __future__ import annotations

import hashlib
import json
import tarfile
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
        # report 时刻 execution dir 还在（成功后即被清）：记录留痕文件此刻的存在。
        self.stderr_trace_seen: list[bool] = []

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.heartbeats_at_report.append(self.heartbeats)
        self.stderr_trace_seen.append(
            (archive.parent / "job" / "runs" / "node_a" / "worker" / "agent-stderr.log").is_file()
        )
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


# -- #748: agent 进程非零退出的可归因（error_message + 留痕文件 + metadata）--


def _events_with_stderr(work_root: Path, stderr_lines: list[str]) -> None:
    """往既有 events.jsonl 前置非 JSON 行（spawn 侧 stderr 合并进 stdout 管道，
    pump 原样落进 events.jsonl——非 JSON 行就是 agent 的 stderr 文本）。"""
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    events = run_dir / "events.jsonl"
    events.write_text("\n".join([*stderr_lines, '{"type":"agent_end"}']) + "\n", encoding="utf-8")


def test_crash_exit_reports_stderr_summary_and_leaves_trace(tmp_path: Path) -> None:
    """非零退出 + stderr 有内容：error_message 带上尾部末行（崩溃头收尾在流的
    最后），run 目录留下 agent-stderr.log，metadata 携带 agent_stderr_tail，
    归档内含该文件。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(
        work_root, ["INFO: boot", "thread panicked at src/main.rs:42:", "assertion failed"]
    )
    client = QueueFakeClient()
    queue = _queue(client)
    archived: dict[str, bytes] = {}
    original_report = client.report

    def report_and_capture(
        execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        # 归档在 report 成功后随 execution dir 一起被清掉，必须在此刻取内容。
        with tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("agent-stderr.log"))
            extracted = tar.extractfile(member)
            assert extracted is not None
            archived[member.name] = extracted.read()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue.submit(_task(work_root, exit_code=1))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    # 尾行才是崩溃头（保尾：INFO: boot 是启动噪音，panic 栈以最后一行收尾）。
    assert report["error_message"] == "Agent process exited 1: assertion failed"
    assert "panicked" in report["agent_stderr_tail"]
    assert report["agent_stderr_tail"].endswith("assertion failed")
    assert any(b"thread panicked" in content for content in archived.values())


def test_crash_exit_without_stderr_keeps_legacy_message(tmp_path: Path) -> None:
    """非零退出 + stderr 无内容：error_message 保持旧形态（只有退出码），
    不写 agent-stderr.log，metadata 不带 agent_stderr_tail。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)  # events.jsonl 只有 JSON 行
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=2))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == "Agent process exited 2"
    assert "agent_stderr_tail" not in report


def test_cancel_exit_130_unchanged_by_stderr(tmp_path: Path) -> None:
    """130 取消语义不被 stderr 污染：即使 stderr 尾部有内容（SIGTERM 残留
    输出），error_message 仍是既定的关机文案，metadata 不带尾部。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["interrupted by signal 15"])
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=130))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "cancelled"
    assert report["error_message"] == "Agent Worker is shutting down"
    assert "agent_stderr_tail" not in report


def test_timeout_exit_124_reports_timeout_not_crash(tmp_path: Path) -> None:
    """124 超时语义独立：error_message 归因到超时（可被 failure_classification
    的 timeout 规则接住），不把半程 stderr 噪音当成崩溃原因。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["still working on it..."])
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=124))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == "Agent process timed out"
    assert "agent_stderr_tail" not in report


def test_completed_exit_zero_writes_stderr_trace_without_failing(tmp_path: Path) -> None:
    """exit 0 + events 里混有非 JSON 行：状态照旧 completed（model-error 扫描
    优先），留痕文件照写（事后排查面），metadata 不带 agent_stderr_tail。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["WARN: deprecation notice"])
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["error_message"] == ""
    assert "agent_stderr_tail" not in report
    # 留痕文件确实落盘（投递成功后 execution dir 已被清掉，断言 report 时刻的观测）。
    assert client.stderr_trace_seen == [True]


# -- #748 review P1/P2：重入幂等（直传回落 / 重启恢复）与出口脱敏 --


def test_direct_upload_fallback_keeps_stderr_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review P1 复现：首趟 prepare（tail 落盘 + 压缩 rewrite）→ 直传失败回落
    → 二趟 prepare 重跑。修复前第二趟扫的是已压缩的 events.jsonl，tail 为空、
    归因全丢；修复后 agent-stderr.log 是幂等锚点，二趟从文件读回。"""
    from worker.artifact.upload import DirectUploadError

    def failing_direct(_path: Path, _spec: object, **_kw: object) -> str:
        raise DirectUploadError("4xx: presigned PUT rejected")

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", failing_direct)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["thread panicked at src/main.rs:42:", "assertion failed"])
    client = QueueFakeClient()
    task = _task(work_root, exit_code=3)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()
    # 二趟 prepare 后 metadata 仍然带尾部摘要 + agent_stderr_tail。
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == "Agent process exited 3: assertion failed"
    assert "thread panicked" in report["agent_stderr_tail"]


def test_result_header_overflow_falls_back_to_archive_embed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#748 R3（codex review P1）队列级复现：128 个产物的直传任务，成功运行
    的直传 ref 清单 ~25KB 撞破 14KB 头预算。修复前 _result_header_value 把清
    单截成前缀（Host 不用截断标记恢复引用 → Missing outputs 改判成功执行）；
    修复后 report 抛 ResultHeaderOverflow，_report 清空直传规格重跑 prepare
    （tar 内嵌产物）、CAS 通道重传全部 128 个产物后以完整 CAS 清单上报。"""
    from worker.host.transfer import ResultHeaderOverflow

    outputs = tuple(f"output-{i:03d}.json" for i in range(128))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    job_dir = work_root / "exec-1" / "job"
    for name in outputs:
        (job_dir / name).write_text("{}", encoding="utf-8")
    attempts = {"n": 0}

    class OverflowFirstReportClient(QueueFakeClient):
        """首趟 report 模拟真实 Client：头序列化对直传形态抛溢出信号。"""

        def __init__(self) -> None:
            super().__init__()
            self.upload_calls = 0

        def upload_artifact(self, path: Path) -> str:
            self.upload_calls += 1  # CAS 通道按文件调用（内容相同时 dict 去重）
            return super().upload_artifact(path)

        def report(self, execution_id, lease_id, metadata, archive):
            attempts["n"] += 1
            return super().report(execution_id, lease_id, metadata, archive)

    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": str(dict(spec)["storage_key"]),
            "size_bytes": 2,
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = OverflowFirstReportClient()
    task = _task(work_root, expected_outputs=outputs)
    task.artifact_uploads = {
        name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs
    }
    # 直传形态的 report：队列把直传 ref 填进 metadata 后调用 client.report，
    # 真实 Client 的头序列化在此抛溢出——模拟之。
    original_report = client.report

    def report_with_overflow(execution_id, lease_id, metadata, archive):
        if any(isinstance(ref, dict) for ref in metadata.get("output_artifacts", {}).values()):
            raise ResultHeaderOverflow("result header over budget with direct-upload refs")
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_with_overflow  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "completed"
    # 回退终点：CAS 形态全量清单（128 条 sha256 字符串），无截断标记。
    assert set(report["output_artifacts"]) == set(outputs)
    assert all(
        isinstance(ref, str) and ref.startswith("sha256:")
        for ref in report["output_artifacts"].values()
    )
    assert "output_artifacts_truncated" not in report
    # 回退后确实重传了 128 个产物（CAS 通道逐文件调用），且只 report 一趟成功。
    assert client.upload_calls == 128
    assert attempts["n"] == 1


def test_restore_reentry_keeps_stderr_attribution(tmp_path: Path) -> None:
    """review P1 复现（restore 路径）：崩溃后重启，marker 恢复的任务重进 bulk
    车道时 events.jsonl 早已压缩——归因必须从 agent-stderr.log 锚点读回。"""
    from worker.upload.prepare import prepare_or_failed

    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["Traceback (most recent call last):", "ValueError: boom"])
    task = _task(work_root, exit_code=1)
    # 首趟 prepare 完成（tail 落盘、events 压缩）——崩溃点在投递前。
    prepare_or_failed(task)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    assert (run_dir / "agent-stderr.log").is_file()
    assert "Traceback" not in (run_dir / "events.jsonl").read_text(encoding="utf-8")
    # 重启恢复：marker 经 restore() 重建 task 重进 bulk 车道（二趟 prepare）。
    marker = work_root / "exec-1" / PENDING_FILENAME
    marker.write_text(json.dumps(task.to_json()), encoding="utf-8")
    client = QueueFakeClient()
    queue = _queue(client)
    assert queue.restore(work_root) == 1
    queue.shutdown()
    report = client.reports[0]
    assert report["error_message"] == "Agent process exited 1: ValueError: boom"
    assert "Traceback" in report["agent_stderr_tail"]


def test_crash_stderr_redacts_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review P2 + R3（codex review P1）：stderr 回显里的密钥字面量（env 值 +
    形态规则）必须在出口面被替换为 ***——error_message、metadata.agent_stderr_tail、
    归档里的 agent-stderr.log。脱敏发生在 sink 落盘时刻（R3：redact 回调注入
    shared 扫描，durable write 前完成——Worker 在落盘后、任何后续重写前退出，
    磁盘上也从无明文密钥；此前是先落盘明文、prepare 后段再就地重写，窗口期内
    崩溃即泄漏）。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "sk-live-supersecretgatewaytoken123")
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(
        work_root,
        [
            "auth failed for key sk-live-supersecretgatewaytoken123",
            "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ3In0.SflKxwRJSMeKKF2QT4fwp",
        ],
    )
    client = QueueFakeClient()
    archived: dict[str, bytes] = {}
    original_report = client.report

    def report_and_capture(
        execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        with tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("agent-stderr.log"))
            extracted = tar.extractfile(member)
            assert extracted is not None
            archived[member.name] = extracted.read()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=5))
    queue.shutdown()
    report = client.reports[0]
    # 面 1+2：error_message（尾行是 Bearer 行，scheme 词保留）+ metadata。
    assert report["error_message"] == "Agent process exited 5: Bearer ***"
    combined = report["error_message"] + report["agent_stderr_tail"]
    assert "sk-live-supersecretgatewaytoken123" not in combined
    assert "SflKxwRJSMeKKF2QT4fwp" not in combined
    assert combined.count("***") >= 2
    # 面 3：归档成员（sink 落盘即脱敏——锚点文件对重入/宿主侧同样安全）。
    [archived_tail] = archived.values()
    assert b"sk-live-supersecretgatewaytoken123" not in archived_tail
    assert b"SflKxwRJSMeKKF2QT4fwp" not in archived_tail
    assert archived_tail.count(b"***") >= 2


def test_anchor_file_on_disk_never_holds_plaintext_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#748 R3（codex review P1）直接证据：脱敏后的字节先落锚点文件——在
    prepare 完成前（模拟 Worker 在 _persist_stderr_tail 返回后、任何后续
    重写前退出）直接读磁盘上的 agent-stderr.log，内容必须已不含密钥。"""
    from worker.upload.prepare import prepare_or_failed

    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "sk-live-supersecretgatewaytoken123")
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, ["auth failed for key sk-live-supersecretgatewaytoken123"])
    task = _task(work_root, exit_code=5)
    # 只跑到 prepare（scan 落盘锚点即停——等价于 Worker 在此后任意时刻崩溃）。
    prepare_or_failed(task)
    sink = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker" / "agent-stderr.log"
    assert sink.is_file()
    content = sink.read_bytes()
    # 落盘文件本身不含密钥：durable write 前已完成脱敏（而非先落盘再重写）。
    assert b"sk-live-supersecretgatewaytoken123" not in content
    assert b"***" in content
    # 无 staging 残留（temp+replace 成功路径不留 .agent-stderr.* 文件）。
    assert list(sink.parent.glob(".agent-stderr.*")) == []


# -- #748 R2 P2-2/P2-3/P3-4：脱敏顺序、配置 environment 通道、规则边界 --


def test_error_message_redacts_before_truncation_no_boundary_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 P2-2 复现（reviewer 实测形态）：密钥跨 200 字符截断边界时，修复前
    先切 [:200] 再脱敏——残段不再匹配完整密钥值，原样漏进 error_message。
    修复后先脱敏再截断：整值替换为 *** 后截断只会切掉 *** 或噪音。"""
    from worker.upload.stderr_evidence import stderr_error_message

    secret = "CI_KEY_" + "k" * 113  # 120 字符密钥
    monkeypatch.setenv("CI_KEY", secret)
    line = "x" * 150 + secret  # 密钥尾部跨过 200 边界
    message = stderr_error_message(7, (line + "\n").encode("utf-8"))
    assert secret not in message
    # 修复前的泄漏形态：[:200] 切在密钥中间，前缀残段（CI_KEY_kkk...）原样
    # 出现在 error_message 外部面。修复后密钥起点起一个字符都不外发。
    assert "CI_KEY_" not in message
    assert "k" * 20 not in message  # 残段主体（连续 k 串）不外发
    assert message.startswith("Agent process exited 7: ")
    assert len(message.split(": ", 1)[1]) <= 200  # 200 语义保持
    assert message.endswith("***")  # 尾部截断落在替换后的 *** 上


def test_error_message_redacts_config_environment_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 P2-3：worker 配置 environment 块（executor 注入 agent 子进程的官方
    secret 通道）里的值，经 register_secrets 注册后三面（error_message、
    metadata.agent_stderr_tail、归档锚点）均替换为 ***——修复前只扫
    os.environ，该通道完全不设防。"""
    from worker.upload import stderr_evidence

    secret = "cfg-gateway-token-ZZZ-not-in-os-environ"
    monkeypatch.setattr(stderr_evidence, "_extra_secret_values", frozenset({secret}))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _events_with_stderr(work_root, [f"failed to authenticate with {secret}"])
    client = QueueFakeClient()
    archived: dict[str, bytes] = {}
    original_report = client.report

    def report_and_capture(
        execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        with tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("agent-stderr.log"))
            extracted = tar.extractfile(member)
            assert extracted is not None
            archived[member.name] = extracted.read()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=9))
    queue.shutdown()
    report = client.reports[0]
    assert report["error_message"] == "Agent process exited 9: failed to authenticate with ***"
    assert secret not in report["agent_stderr_tail"]
    [archived_tail] = archived.values()
    assert secret.encode() not in archived_tail


def test_redact_secrets_replaces_longest_value_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 P3-4：短密钥是长密钥前缀时，先替换长的——否则短值先替换掉前缀，
    长密钥只剩不可恢复的残段。"""
    from worker.upload.stderr_evidence import redact_secrets

    short, long = "tok-live-abc123", "tok-live-abc123def456ghi789"
    monkeypatch.setenv("SHORT_TOKEN", short)
    monkeypatch.setenv("LONG_TOKEN", long)
    text = f"keys: {short} and {long}"
    redacted = redact_secrets(text)
    assert "def456ghi789" not in redacted  # 长密钥残段不可残留
    assert redacted.count("***") == 2


def test_redact_secrets_byte_threshold_covers_cjk_short_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 P3-4：8 个 CJK 字 = 24 字节的真实密钥不再因「字符>8」阈值漏掉
    （阈值改为字节>8）。"""
    from worker.upload.stderr_evidence import redact_secrets

    cjk_secret = "九曜之门钥匙甲乙"  # 8 个 CJK 字符（24 字节）
    monkeypatch.setenv("GATEWAY_KEY", cjk_secret)
    redacted = redact_secrets(f"gateway={cjk_secret}")
    assert cjk_secret not in redacted


def test_redact_secrets_covers_github_and_slack_shapes() -> None:
    """R2 P3-4：形态规则补 GitHub PAT/OAuth（ghp_/gho_）与 Slack
    bot/user/app token（xox[bap]-）三族。"""
    from worker.upload.stderr_evidence import redact_secrets

    for secret in (
        "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
        "gho_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
        "xoxb-" + "123456789012-abcdef",
        "xoxa-" + "123456789012-abcdef",
        "xoxp-" + "123456789012-abcdef",
    ):
        assert secret not in redact_secrets(f"echo {secret} failed")


def test_sink_persist_failure_cleans_staging_and_never_fails_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#748 R3（codex review P1）staging 残留：os.replace 失败（如跨设备/目标
    被占用）时 delete=False 的临时文件必须被清理——修复前 staging 永久残留在
    run 目录里（且随归档外发）。落盘失败本身仍是 best-effort：压缩照常完成、
    返回值照常携带（脱敏后的）tail。"""
    import os as _os

    from shared import pi_events
    from worker.upload.stderr_evidence import AGENT_STDERR_FILENAME

    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = run_dir / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\nauth failed for {secret}\n', encoding="utf-8")
    real_replace = _os.replace  # patch 前捕获原始实现

    def failing_replace(src, dst):
        # 只对 sink 锚点失败（压缩 rewrite 自身的 replace 不受影响）。
        if str(dst).endswith(AGENT_STDERR_FILENAME):
            raise OSError("cross-device link")  # replace 失败：staging 必须被清理
        return real_replace(src, dst)

    monkeypatch.setattr(pi_events.os, "replace", failing_replace)
    _, original, compressed, tail = pi_events.scan_and_compress_pi_events(
        events,
        stderr_sink=run_dir / AGENT_STDERR_FILENAME,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
    )
    assert original > 0 and compressed > 0  # 压缩未因 sink 失败中断
    # 返回值保持 RAW（调用方自行脱敏自己的出口面——shared 只管落盘脱敏）。
    assert tail == f"auth failed for {secret}".encode()
    # staging 已清理；sink 未落盘（replace 失败，无半截文件）。
    assert list(run_dir.glob(".agent-stderr.*")) == []
    assert not (run_dir / AGENT_STDERR_FILENAME).exists()


def test_sink_replace_success_leaves_no_staging(tmp_path: Path) -> None:
    """成功路径对照：replace 成功后 run 目录里只有锚点文件，无 staging 残留。"""
    from shared import pi_events

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = run_dir / "events.jsonl"
    events.write_text('{"type":"session"}\npanic: real cause\n', encoding="utf-8")
    sink = run_dir / "agent-stderr.log"
    pi_events.scan_and_compress_pi_events(events, stderr_sink=sink)
    assert sink.read_bytes() == b"panic: real cause"
    assert list(run_dir.glob(".agent-stderr.*")) == []
