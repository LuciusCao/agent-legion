"""Bounded, disk-backed result upload queue for Agent Worker executions.

Decouples "the Agent process finished" from "the result reached the Host":
the execution thread releases its slot at process exit and hands everything
after that point — model-error scan, events compression, archive build,
artifact upload, result report — to this queue. Upload concurrency stays
small (default 4) so a completion wave of dozens of executions never turns
into a transfer storm against the Host.

Two lanes share one scheduler (worker/upload_scheduler.py): the bulk lane
runs prepare + artifact uploads, the report lane runs the final report and
is drained strictly first, so a completion wave cannot delay small reports
behind other tasks' bulk transfers. The lane limit is hot-adjustable via
``set_max_concurrency``.

Durability: every task writes an ``upload_pending.json`` marker into its
execution dir before entering the queue; the marker is removed only after
the Host accepts the result. A crashed Worker rescans it on startup and
re-enters through the bulk lane (artifact stores are content-addressed, so
re-upload is harmless).

Lease ownership: the per-execution heartbeat started at claim time keeps
beating through the upload. It is quiesced for the final report and resumed
only while a transient report failure backs off (worker/upload_heartbeat.py).
"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from worker import upload_heartbeat
from worker._atomic import atomic_write
from worker._retry import run_with_retry
from worker.artifact_upload import DirectUploadError, upload_artifact_direct
from worker.host_transfer import HostRequestError
from worker.runtime_controls import MAX_DYNAMIC_CONCURRENCY

# MAX_ERROR_MESSAGE_CHARS 的定义在 upload_prepare（failed_metadata 的截断
# 上限）；execution_run 沿本模块导入，`as` 惯用法重导出而非再定义一份
# 副本（#200/#201 同族的 sync-by-comment 反模式）。
from worker.upload_prepare import (
    MAX_ERROR_MESSAGE_CHARS as MAX_ERROR_MESSAGE_CHARS,
)
from worker.upload_prepare import (
    failed_metadata,
    prepare_or_failed,
)
from worker.upload_scheduler import LaneScheduler

PENDING_FILENAME = "upload_pending.json"
_PENDING_VERSION = 1


class PendingUploadExists(RuntimeError):
    """#203：execution dir 已带未投递 marker——该目录归 UploadQueue 所有。"""


_RETRY_BASE_SECONDS = 2.0
_RETRY_CAP_SECONDS = 60.0
_HEARTBEAT_JOIN_SECONDS = 5.0


@dataclass
class UploadTask:
    """Everything needed to deliver one execution's result to the Host."""

    execution_id: str
    lease_id: str
    execution_dir: Path
    node_key: str
    status_fields: dict[str, str]
    # "process": run post-processing (scan/compress/archive) then report.
    # "prebuilt": metadata is final (pre-process failure / pre-start cancel).
    kind: str
    # "agent"（缺省）或 "code"（批次 2）：上面的 kind 已被
    # "process"/"prebuilt" 占用，agent/code 维度用 exec_kind 表达（勿复用）。
    exec_kind: str = "agent"
    # code 执行的结果（status/error_message/auth_failure_connection），由
    # code_runner 在进程退出后填入；随 pending marker 持久化供崩溃恢复。
    code_result: dict[str, Any] | None = None
    exit_code: int = 1
    expected_outputs: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    prebuilt_metadata: dict[str, Any] | None = None
    # #160 D12: claim manifest 的 artifact_uploads（name → {storage_key, url}
    # presigned PUT）。非空时产物直传 S3、result.tar.gz 不再内嵌产物；
    # 空 = 旧通道（CAS POST + tar 内嵌）。不持久化：presigned URL 会过期，
    # 崩溃恢复的任务从 bulk 车道重进时走旧通道（Host 两种形态都收）。
    artifact_uploads: dict[str, Any] = field(default_factory=dict)
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    heartbeat_thread: threading.Thread | None = None
    # bulk 车道产物，交给 report 车道；运行时状态，不持久化——崩溃恢复的任务
    # 一律从 bulk 车道重进，prepare 与 artifact 上传会原样重做。
    prepared_metadata: dict[str, Any] | None = None
    prepared_archive: Path | None = None

    def is_direct_upload(self, outputs: list[str]) -> bool:
        """#160 D12 直传判定（#201 单点收敛）：manifest 带 artifact_uploads 且
        每个产出都有上传规格才走直传 S3 通道；否则整体回落旧通道（CAS POST +
        tar 内嵌）。归档是否内嵌产物（upload_prepare / code_runner 的
        prepare_result）与上传通道（_bulk_transfer）必须用同一判定，否则产物
        既不在 tar 里也没直传。注意 DirectUploadError 的回落路径会把
        artifact_uploads 清空再重取判定，本方法天然随之变 False。"""
        return bool(self.artifact_uploads) and all(
            name in self.artifact_uploads for name in outputs
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": _PENDING_VERSION,
            "execution_id": self.execution_id,
            "lease_id": self.lease_id,
            "node_key": self.node_key,
            "status_fields": self.status_fields,
            "kind": self.kind,
            "exec_kind": self.exec_kind,
            "code_result": self.code_result,
            "exit_code": self.exit_code,
            "expected_outputs": list(self.expected_outputs),
            "command": list(self.command),
            "prebuilt_metadata": self.prebuilt_metadata,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any], work_root: Path) -> UploadTask:
        if int(payload.get("version", 0)) != _PENDING_VERSION:
            raise ValueError(f"unsupported upload marker version: {payload.get('version')!r}")
        execution_id = str(payload["execution_id"])
        return cls(
            execution_id=execution_id,
            lease_id=str(payload["lease_id"]),
            execution_dir=work_root / execution_id,
            node_key=str(payload["node_key"]),
            status_fields={str(k): str(v) for k, v in dict(payload["status_fields"]).items()},
            kind=str(payload["kind"]),
            exec_kind=str(payload.get("exec_kind") or "agent"),
            code_result=payload.get("code_result"),
            exit_code=int(payload.get("exit_code", 1)),
            expected_outputs=tuple(str(name) for name in payload.get("expected_outputs", [])),
            command=tuple(str(part) for part in payload.get("command", [])),
            prebuilt_metadata=payload.get("prebuilt_metadata"),
        )


class UploadQueue:
    def __init__(
        self,
        client: Any,
        status: Any,
        *,
        max_concurrency: int = 4,
        heartbeat_interval: float = 15.0,
        stop: threading.Event | None = None,
    ) -> None:
        self._client = client
        self._status = status
        self._heartbeat_interval = heartbeat_interval
        self._stop = stop if stop is not None else threading.Event()
        self._scheduler = LaneScheduler(
            MAX_DYNAMIC_CONCURRENCY, max_concurrency, thread_name_prefix="agent-upload"
        )
        self._lock = threading.Lock()
        self._depth = 0

    @property
    def depth(self) -> int:
        """Tasks queued or in flight; the claim loop reads this for backpressure."""
        with self._lock:
            return self._depth

    def set_max_concurrency(self, value: int) -> None:
        """热更新上传并发：调大立即补位，调小不抢占、在途任务自然跑完。"""
        self._scheduler.set_limit(value)

    def submit(self, task: UploadTask) -> None:
        """Persist the pending marker, then queue the delivery.

        Marker first: a crash between the two loses at most the result (the
        Host requeues after the lease expires), never reports twice.
        """
        marker = task.execution_dir / PENDING_FILENAME
        # 原子写：崩溃留下半截 JSON 会被 restore() 当成损坏 marker 而 rmtree 整个
        # 执行目录，丢掉已完成的结果。
        atomic_write(marker, json.dumps(task.to_json(), ensure_ascii=False))
        # upsert: 重启恢复的任务在 reporter 里尚无条目，积压期间也要以 queued_upload 可见。
        self._status.upsert_phase(task.execution_id, "queued_upload", **task.status_fields)
        with self._lock:
            self._depth += 1
        self._scheduler.submit(lambda: self._deliver_bulk(task))

    def restore(self, work_root: Path) -> int:
        """Re-queue executions whose results never reached the Host."""
        restored = 0
        try:
            children = sorted(work_root.iterdir())
        except OSError:
            return 0
        for child in children:
            marker = child / PENDING_FILENAME
            if not child.is_dir() or not marker.is_file():
                continue
            try:
                task = UploadTask.from_json(
                    json.loads(marker.read_text(encoding="utf-8")), work_root
                )
            except Exception as exc:
                print(f"discarding unreadable upload marker {marker}: {exc}", flush=True)
                shutil.rmtree(child, ignore_errors=True)
                continue
            self.submit(task)
            restored += 1
        return restored

    def shutdown(self) -> None:
        # Tasks watch the shared stop event and bail out of retry loops quickly.
        self._scheduler.shutdown()

    def _deliver_bulk(self, task: UploadTask) -> None:
        """bulk 车道入口：prepare + artifact 上传，完成后挂入 report 车道。"""
        if task.heartbeat_thread is None:
            # Restored from disk: resume heartbeating so the lease survives.
            # The status entry already exists — submit() upserted it at restore.
            task.heartbeat_thread = upload_heartbeat.start_upload_heartbeat(
                self._client,
                task.execution_id,
                task.lease_id,
                task.heartbeat_stop,
                self._heartbeat_interval,
            )
        try:
            ready = self._bulk_transfer(task)
        except Exception as exc:
            print(f"upload task crashed for {task.execution_id}: {exc}", flush=True)
            ready = False
        if ready:
            try:
                # 心跳保持跳动直到 report 前才 quiesce：report 车道排队期间
                # 租约仍需 proof of life。
                self._scheduler.submit(lambda: self._deliver_report(task), priority=True)
            except RuntimeError:
                pass  # 调度器已关停；marker 留给下次启动恢复
            else:
                return
        self._finalize(task)

    def _deliver_report(self, task: UploadTask) -> None:
        """report 车道入口：quiesce 心跳 → report → 删 marker 清目录。"""
        try:
            self._report(task)
        except Exception as exc:
            print(f"upload report crashed for {task.execution_id}: {exc}", flush=True)
        finally:
            self._finalize(task)

    def _finalize(self, task: UploadTask) -> None:
        upload_heartbeat.quiesce_heartbeat(task.heartbeat_stop, task.heartbeat_thread, 2)
        self._status.finish(task.execution_id)
        with self._lock:
            self._depth -= 1

    def _bulk_transfer(self, task: UploadTask) -> bool:
        """prepare + artifact 上传；True = 可进 report 车道，False = 中止（marker 保留）。"""
        if self._stop.is_set():
            return False  # never started; marker intact for the next startup
        self._status.set_phase(task.execution_id, "uploading")
        job_dir = task.execution_dir / "job"
        metadata, archive, outputs = prepare_or_failed(task)
        # #160 D12：直传判定经 UploadTask.is_direct_upload（#201 单点收敛）；
        # 直传走 presigned PUT，否则整体回落旧通道（CAS POST + tar 内嵌，tar
        # 已在 prepare_result 按同一方法决定是否内嵌）。
        direct = task.is_direct_upload(outputs)
        while True:
            uploaded: dict[str, Any] = {}
            restart = False
            for name in outputs:
                try:
                    if direct:
                        ref = upload_artifact_direct(
                            job_dir / PurePosixPath(name),
                            task.artifact_uploads[name],
                            stop=self._stop,
                        )
                    else:
                        ref = self._upload_with_retry(job_dir / PurePosixPath(name))
                except DirectUploadError as exc:
                    # 直传失败（4xx / 重试耗尽 / 规格畸形）不判 run failed：清掉
                    # 上传规格重跑 prepare（tar 自动内嵌产物），重启循环走无限
                    # 重试的 CAS 通道，与无规格任务同一语义。
                    print(f"direct upload failed for {task.execution_id}: {exc}", flush=True)
                    task.artifact_uploads = {}
                    metadata, archive, outputs = prepare_or_failed(task)
                    direct = False
                    restart = True
                    break
                except HostRequestError as exc:
                    # Terminal 4xx on the artifact itself: report the run failed
                    # instead of looping forever on a verdict that cannot change.
                    metadata = failed_metadata(task, str(exc))
                    uploaded = {}
                    break
                if ref is None:
                    return False  # shutting down mid-upload; marker stays for restore
                uploaded[name] = ref
            if not restart:
                break
        metadata["output_artifacts"] = uploaded
        task.prepared_metadata = metadata
        task.prepared_archive = archive
        return True

    def _report(self, task: UploadTask) -> None:
        metadata = task.prepared_metadata or {}
        archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
        # Quiesce the heartbeat before the final report: a beat racing the
        # commit loses the row lock and logs a spurious "lost ownership" 409.
        # Resume only while a transient report failure backs off.
        # Deliberately NOT run_with_retry: each backoff window must re-arm the
        # lease heartbeat, which the shared plain-sleep loop cannot express.
        upload_heartbeat.quiesce_task_heartbeat(task, _HEARTBEAT_JOIN_SECONDS)
        backoff = _RETRY_BASE_SECONDS
        while not self._stop.is_set():
            try:
                status_code, body = self._client.report(
                    task.execution_id, task.lease_id, metadata, archive
                )
            except RuntimeError as exc:
                print(
                    f"result report retry for {task.execution_id}: {exc}",
                    flush=True,
                )
                # An unbounded backoff chain can outlive the lease TTL.
                task.heartbeat_stop = threading.Event()
                task.heartbeat_thread = upload_heartbeat.start_upload_heartbeat(
                    self._client,
                    task.execution_id,
                    task.lease_id,
                    task.heartbeat_stop,
                    self._heartbeat_interval,
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, _RETRY_CAP_SECONDS)
                upload_heartbeat.quiesce_task_heartbeat(task, _HEARTBEAT_JOIN_SECONDS)
                continue
            if status_code == 204:
                break
            # 409: lease gone (Host swept/requeued) — the result is moot.
            # Other 4xx: the Host rejected the payload itself; keep the log,
            # drop the result, never retry a verdict.
            print(
                f"result report rejected for {task.execution_id}:"
                f" HTTP {status_code}: {body[:200]!r}",
                flush=True,
            )
            break
        else:
            return  # stopped before the report resolved; marker stays
        marker = task.execution_dir / PENDING_FILENAME
        marker.unlink(missing_ok=True)
        shutil.rmtree(task.execution_dir, ignore_errors=True)

    def _upload_with_retry(self, path: Path) -> str | None:
        """Upload one artifact; None = stopped (retry next startup); 4xx propagates."""
        return run_with_retry(
            lambda: self._client.upload_artifact(path),
            retriable=(RuntimeError,),
            terminal=(HostRequestError,),
            base_seconds=_RETRY_BASE_SECONDS,
            cap_seconds=_RETRY_CAP_SECONDS,
            stop=self._stop,
            on_retry=lambda exc, _backoff: print(
                f"artifact upload retry for {path.name}: {exc}", flush=True
            ),
        )
