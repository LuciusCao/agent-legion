"""Unit tests for the Worker upload queue (worker/upload/queue.py).

stderr 归因/脱敏一族在姊妹文件 tests/workers/test_worker_upload_stderr.py
（同一条 800 行拆分线）；共享桩/工具见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
)
from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop
from worker.execution.ownership import OWNER_FILENAME, write_owner_marker
from worker.status import ExecutionStatusReporter
from worker.upload import queue as upload_queue
from worker.upload.queue import PENDING_FILENAME, UploadQueue, UploadTask


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


# -- #748 R3 / #755 codex P1：结果头预算溢出（直传 ref 形态）的清单归档回退 --


def _overflow_then_capture(client: QueueFakeClient, captured: dict[str, Any]) -> None:
    """模拟真实 Client 的头序列化：直传 dict ref 形态抛溢出信号；重报（清单
    已清空 + in_archive 标记）不再抛——与 worker.host.transfer 的信号纪律
    一致。顺带捕获第二趟 report 的 metadata、归档成员清单与清单成员内容。"""
    import tarfile

    from worker.host.transfer import ResultHeaderOverflow

    original_report = client.report

    def report_with_overflow(execution_id, lease_id, metadata, archive):
        captured["attempts"] = captured.get("attempts", 0) + 1
        if any(isinstance(ref, dict) for ref in metadata.get("output_artifacts", {}).values()):
            raise ResultHeaderOverflow("result header over budget with direct-upload refs")
        captured["metadata"] = dict(metadata)
        with tarfile.open(archive) as tar:
            captured["members"] = tar.getnames()
            # embed 失败路径归档保持原形态（无清单成员）：按名字探测而非
            # extractfile 直接取（缺成员抛 KeyError）。
            member = (
                tar.extractfile("result-output-artifacts.json")
                if "result-output-artifacts.json" in captured["members"]
                else None
            )
            captured["manifest"] = json.loads(member.read()) if member is not None else None
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_with_overflow  # type: ignore[method-assign]


def test_result_header_overflow_embeds_manifest_in_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 codex P1 队列级复现：128 个产物的直传任务，直传 ref 清单 ~25KB
    撞破 14KB 头预算。新协议：产物字节不动（已在 S3，零 CAS 重传），完整
    direct-ref 清单写成归档首成员 result-output-artifacts.json，头里只带
    output_artifacts_in_archive 标记，重报即投递成功。"""
    outputs = tuple(f"output-{i:03d}.json" for i in range(128))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    job_dir = work_root / "exec-1" / "job"
    for name in outputs:
        (job_dir / name).write_text("{}", encoding="utf-8")

    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": str(dict(spec)["storage_key"]),
            "size_bytes": 2,
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs)
    specs = {name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs}
    task.artifact_uploads = dict(specs)
    captured: dict[str, Any] = {}
    _overflow_then_capture(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2  # 溢出 → embed → 重报，恰两趟
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    # 头里只剩标记：清单清空、无截断标记（截断是 CAS 形态的最后手段）。
    assert report["output_artifacts"] == {}
    assert report["output_artifacts_in_archive"] is True
    assert "output_artifacts_truncated" not in report
    # 归档首成员即完整 direct-ref 清单（Host 流式扫描几 KB 即命中）。
    assert captured["members"][0] == "result-output-artifacts.json"
    assert captured["manifest"] == {
        name: {"storage_key": f"jobs-staging/x/{name}", "size_bytes": 2, "content_hash": "a" * 64}
        for name in outputs
    }
    # 产物字节零重传：CAS 通道从未被调用；直传规格保持不动。
    assert client.uploads == {}
    assert task.artifact_uploads == specs
    # 重报 204：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


def test_result_header_overflow_embed_failure_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """embed 失败（OSError/tarfile/契约违例）→ 诚实判败：failed_metadata
    上报（清单不可交付即产物引用不可用），归档保持原形态，不重试不死循环。"""
    from worker.upload import report as report_module

    def failing_embed(archive, artifacts, expected_outputs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(report_module, "embed_output_artifacts_manifest", failing_embed)
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": "jobs-staging/x/output.json",
            "size_bytes": 2,
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = QueueFakeClient()
    task = _task(work_root)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    captured: dict[str, Any] = {}
    _overflow_then_capture(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "manifest embed failed" in report["error_message"]
    assert report["output_artifacts"] == {}
    assert client.uploads == {}  # 零 CAS 重传
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


# -- #755 codex P1：DirectUploadError 换轨预检（上限来自 claim 下发） --


def _direct_upload_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """直传通道终态失败（DirectUploadError）→ 触发队列的换轨判定。"""
    from worker.artifact.upload import DirectUploadError

    def direct_upload_fails(path: Path, spec: object, **_kw: object) -> dict:
        raise DirectUploadError("HTTP 403")

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_fails)


def test_direct_upload_fallback_oversize_embed_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换轨预检：内嵌总量（产物 + run_dir 实测）超「claim 下发的
    max_archive_bytes − 安全余量」时**不换轨**——重内嵌会把大产出人群送进
    Host 413 → 丢结果 → 租约过期全量重跑（每轮同样 413）。本地诚实判败：
    failed_metadata 上报，归档保持直传形态（产物字节本就不在 tar，events/
    日志照常携带），直传规格保留、CAS 通道零上传。"""
    from worker.upload import embed_precheck

    # 用小常量代替 1 MiB 余量，避免 tmp 盘写大文件（总量口径与上限同源断言）。
    monkeypatch.setattr(embed_precheck, "EMBED_SAFETY_MARGIN_BYTES", 0)
    outputs = tuple(f"output-{i:03d}.json" for i in range(3))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    job_dir = work_root / "exec-1" / "job"
    for name in outputs:
        (job_dir / name).write_bytes(b"\0" * 1024)  # 3 KiB > 1 KiB 下发上限
    run_dir_bytes = sum(
        p.stat().st_size for p in (job_dir / "runs" / "node_a" / "worker").rglob("*") if p.is_file()
    )

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs, max_archive_bytes=1024)
    task.artifact_uploads = {
        name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs
    }
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "archive-embed ceiling" in report["error_message"]
    # 未压缩口径的总量如实上报：产物 + run_dir 实测。
    assert f"totals {3072 + run_dir_bytes} bytes" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 未换轨：直传规格保留、CAS 通道零上传。
    assert task.artifact_uploads
    assert client.uploads == {}
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_within_ceiling_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内嵌总量 ≤ 下发上限 − 余量时正常换轨：清规格重跑 prepare（tar 内嵌
    产物）、CAS 通道上传全部产物、CAS 形态清单上报完成。"""
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root, max_archive_bytes=64 * 1024 * 1024)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    # 换轨终点：CAS 形态清单 + 产物字节确实重传。
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    assert len(client.uploads) == 1
    assert not task.artifact_uploads  # 规格已清
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_default_ceiling_without_claim_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 Host 未下发（max_archive_bytes=0）→ 预检回落 64 MiB 默认：小产物
    照常换轨，与无规格任务同一语义。"""
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root)  # max_archive_bytes 默认 0
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert client.reports[0]["status"] == "completed"
    assert len(client.uploads) == 1


def test_direct_upload_fallback_missing_output_does_not_block_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 对抗复审 P3：缺席的 expected output 按 0 字节计（不内嵌任何字节，
    Host 侧 Missing outputs 判定不受影响），预检不得把它当「大小未知」拒绝
    换轨——否则会拿体积措辞误导排障。"""
    outputs = ("output.json", "gone.json")
    work_root = tmp_path / "work"
    _execution_dir(work_root)  # 只造 output.json；gone.json 缺席按 0 字节计

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    assert "could not be stat'ed" not in report["error_message"]


def test_direct_upload_fallback_unstattable_output_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预检的 stat 失败臂：文件存在但 stat 抛 OSError（权限/IO 错误）→ 大小
    未知即不可证安全，拒绝换轨、本地诚实判败。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    # 投弹窗口必须收窄到预检内部：全局 patch Path.stat 会波及 prepare 的
    # is_file()（裸 OSError 无 errno，不在 pathlib 可忽略族而重抛），初次
    # prepare 就降级成 "result preparation failed"，永远走不到换轨判定。
    # 用 threading.local 武装窗口（队列跑在调度池线程），炸弹只落在
    # embed_precheck 对 expected output 的 stat 上。
    bomb = threading.local()
    real_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> Any:
        if getattr(bomb, "armed", False) and self.name == "output.json":
            raise OSError("permission denied")
        return real_stat(self, *args, **kwargs)

    real_rejection = upload_queue.embed_switch_rejection

    def armed_rejection(task: UploadTask) -> str | None:
        bomb.armed = True
        try:
            return real_rejection(task)
        finally:
            bomb.armed = False

    monkeypatch.setattr(Path, "stat", flaky_stat)
    monkeypatch.setattr(upload_queue, "embed_switch_rejection", armed_rejection)
    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "could not be stat'ed" in report["error_message"]
    assert "archive-embed ceiling" in report["error_message"]
    assert client.uploads == {}  # 未换轨
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_code_lane_node_log_counted_in_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code 车道的 node.log 写在 execution_dir 根（不在 run_dir），是沙箱
    stdout/stderr 的无上限捕获，可以是内嵌 tar 的最大成员。预检不计入它，
    「大且不可压缩的 node.log + 小产物」会被放进换轨，重备 tar 超 Host
    上限 → 413 → 丢结果 → 全量重跑死循环。"""
    from worker.upload import embed_precheck

    monkeypatch.setattr(embed_precheck, "EMBED_SAFETY_MARGIN_BYTES", 0)
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    # 产物只有 2 字节（output.json "{}"）；node.log 一个就超预检上限。
    (execution_dir / "node.log").write_bytes(b"\0" * 2048)
    run_dir_bytes = sum(
        p.stat().st_size
        for p in (execution_dir / "job" / "runs" / "node_a" / "worker").rglob("*")
        if p.is_file()
    )

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, max_archive_bytes=1024)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "archive-embed ceiling" in report["error_message"]
    # 总量口径含 node.log：产物 2B + run_dir 实测 + node.log 2048B。
    assert f"totals {2 + run_dir_bytes + 2048} bytes" in report["error_message"]
    assert task.artifact_uploads  # 未换轨
    assert client.uploads == {}
    assert not (work_root / "exec-1").exists()
