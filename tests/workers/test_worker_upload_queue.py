"""Unit tests for the Worker upload queue (worker/upload/queue.py)."""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading
from pathlib import Path

import pytest

from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop
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
    """review P2：stderr 回显里的密钥字面量（env 值 + 形态规则）必须在三个
    出口面被替换为 ***——error_message、metadata.agent_stderr_tail、归档里的
    agent-stderr.log（脱敏发生在 sink 落盘时刻，锚点文件本身就不含密钥）。"""
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


def test_sink_rewrite_failure_truncates_anchor_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 P3-3(b)：锚点重写失败（sink 不可写）时降级为截断为空——宁丢证据
    不外发密钥（修复前裸密钥文件进归档）。"""
    from pathlib import Path as _Path

    from worker.upload import stderr_evidence

    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sink = run_dir / stderr_evidence.AGENT_STDERR_FILENAME
    sink.write_bytes(f"auth failed for {secret}".encode())
    real_write_bytes = _Path.write_bytes
    writes: list[bytes] = []

    def always_fails(self: Path, data: bytes) -> int:
        writes.append(data)
        raise OSError("disk full")  # 重写与截断都失败：最后一级防御失效形态

    monkeypatch.setattr(_Path, "write_bytes", always_fails)
    tail = stderr_evidence.stderr_tail_for_run(run_dir, b"")
    # 内存返回值仍是脱敏后的 tail（证据面丢的是锚点文件，不是 metadata）。
    assert secret.encode() not in tail
    assert b"***" in tail
    # 两次落盘尝试（脱敏形态 + 空形态），写出的字节面从未含密钥。
    assert len(writes) == 2
    assert b"***" in writes[0] and secret.encode() not in writes[0]
    assert writes[1] == b""

    # 截断成功形态：重写失败、截断成功 → 锚点终态为空文件。
    monkeypatch.setattr(_Path, "write_bytes", real_write_bytes)

    def first_fails_only(self: Path, data: bytes) -> int:
        if b"***" in data:
            raise OSError("first write fails")
        return real_write_bytes(self, data)

    monkeypatch.setattr(_Path, "write_bytes", first_fails_only)
    stderr_evidence.stderr_tail_for_run(run_dir, b"")
    assert sink.read_bytes() == b""  # 截断成功 → 锚点为空，无密钥外发
