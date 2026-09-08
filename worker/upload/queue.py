"""Bounded, disk-backed result upload queue for Agent Worker executions.

Decouples "the Agent process finished" from "the result reached the Host":
the execution thread releases its slot at process exit and hands everything
after that point — model-error scan, events compression, archive build,
artifact upload, result report — to this queue. Upload concurrency stays
small (default 4) so a completion wave of dozens of executions never turns
into a transfer storm against the Host.

Two lanes share one scheduler (worker/upload/scheduler.py): the bulk lane
runs prepare + artifact uploads, the report lane runs the final report and
is drained strictly first, so a completion wave cannot delay small reports
behind other tasks' bulk transfers. The lane limit is hot-adjustable via
``set_max_concurrency``.

Durability: every task writes an ``upload_pending.json`` marker into its
execution dir before entering the queue; the marker is removed only after
the Host accepts the result. A crashed Worker rescans it on startup and
re-enters through the bulk lane (artifact stores are content-addressed, so
re-upload is harmless).

Lease ownership: the lease heartbeat keeps beating through the upload
(per-execution threads before #352; the per-Worker batch registry after). It
is quiesced for the final report and resumed only while a transient report
failure backs off (worker/upload/heartbeat.py).
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from worker._atomic import atomic_write
from worker._retry import run_with_retry
from worker.artifact.upload import DirectUploadError, upload_artifact_direct
from worker.host.transfer import HostRequestError
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY
from worker.upload import heartbeat as upload_heartbeat
from worker.upload import report_events
from worker.upload.task import PendingUploadExists as PendingUploadExists
from worker.upload.task import UploadTask

if TYPE_CHECKING:
    from worker.execution.heartbeat_batch import BatchHeartbeatRegistry

# MAX_ERROR_MESSAGE_CHARS 的定义在 result_metadata（failed_metadata 的截断
# 上限）；execution_run 沿本模块导入，`as` 惯用法重导出而非再定义一份
# 副本（#200/#201 同族的 sync-by-comment 反模式）。failed_metadata 经
# prepare 重导出（prepare_or_failed 同车）。
from worker.upload.prepare import (
    failed_metadata,
    prepare_or_failed,
)
from worker.upload.result_metadata import (
    MAX_ERROR_MESSAGE_CHARS as MAX_ERROR_MESSAGE_CHARS,
)
from worker.upload.scheduler import LaneScheduler

PENDING_FILENAME = "upload_pending.json"

_RETRY_BASE_SECONDS = 2.0
_RETRY_CAP_SECONDS = 60.0
_HEARTBEAT_JOIN_SECONDS = 5.0


class UploadQueue:
    def __init__(
        self,
        client: Any,
        status: Any,
        *,
        max_concurrency: int = 4,
        heartbeat_interval: float = 15.0,
        stop: threading.Event | None = None,
        heartbeat_registry: BatchHeartbeatRegistry | None = None,
    ) -> None:
        self._client = client
        self._status = status
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_registry = heartbeat_registry
        self._stop = stop if stop is not None else threading.Event()
        self._scheduler = LaneScheduler(
            MAX_DYNAMIC_CONCURRENCY, max_concurrency, thread_name_prefix="agent-upload"
        )
        self._lock = threading.Lock()
        self._depth = 0

    def set_heartbeat_registry(self, registry: BatchHeartbeatRegistry) -> None:
        """Attach the per-Worker batch heartbeat coordinator (#352), once,
        after construction; queued upload tasks register their leases with
        it instead of owning single-beat threads."""
        self._heartbeat_registry = registry

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
        task.report_timer = report_events.UploadReportTimer()
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
                # #204 broad-except audit: 逐目录遏制。marker 的逃逸族混族
                # ——解码 ValueError、from_json 的 KeyError/TypeError（字段
                # 畸形）、read_text 的 OSError——统一语义是"marker 已损坏"。
                # 吞是对的：一个坏 marker 不得阻断其余待恢复结果重新入队；
                # marker 经 atomic_write 落盘（tmp+fsync+replace），读不出
                # 即真损坏而非半截写，rmtree 丢弃该目录是设计选择。日志
                # 保全：print 记录 marker 路径与异常。
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
        report_events.mark(task, "bulk_start")
        if task.heartbeat_thread is None:
            # Restored from disk: resume heartbeating so the lease survives.
            # The status entry already exists — submit() upserted it at restore.
            task.heartbeat_registry = self._heartbeat_registry
            task.heartbeat_thread = upload_heartbeat.start_upload_heartbeat(
                self._client, task, self._heartbeat_interval
            )
        try:
            ready = self._bulk_transfer(task)
        except Exception as exc:
            # #204 broad-except audit: bulk 车道任务的存活安全网。
            # _bulk_transfer 的已知失败族（DirectUploadError 回落、
            # HostRequestError 终态、传输重试）都在内部处理，逃到这里的是
            # 意外路径——但车道函数跑在 LaneScheduler 的池线程里，未捕获
            # 异常会落进无人读取的 Future 而完全静默，这里既是安全网也是
            # 唯一日志点。吞是对的：ready=False 走 _finalize（心跳 quiesce、
            # 深度记账），pending marker 原样保留，下次启动 restore 重新
            # 投递。日志保全：print 记录 execution_id 与异常（仅消息、无
            # 堆栈，见 #298 审计报告的观察项）。
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
        self._finalize(task, "aborted")

    def _deliver_report(self, task: UploadTask) -> None:
        """report 车道入口：quiesce 心跳 → report → 删 marker 清目录。"""
        report_events.mark(task, "report_start")
        outcome = "aborted"
        try:
            outcome = self._report(task)
        except Exception as exc:
            # #204 broad-except audit: report 车道任务的存活安全网（同
            # _deliver_bulk：逃逸异常会落进无人读取的 Future，这里是唯一
            # 日志点）。_report 内部已处理重试族（RuntimeError 退避重试）
            # 与非 204 终态，逃到这里的是意外路径（如 marker.unlink 的
            # OSError）。吞是对的：finally 的 _finalize 必须执行——心跳
            # quiesce 与队列深度记账——marker 未删则下次启动 restore 重新
            # 投递（重复 report 由 Host 侧租约 409 幂等拒绝）。日志保全：
            # print 记录 execution_id 与异常（仅消息、无堆栈）。
            print(f"upload report crashed for {task.execution_id}: {exc}", flush=True)
        finally:
            self._finalize(task, outcome)

    def _finalize(self, task: UploadTask, outcome: str) -> None:
        upload_heartbeat.prune_heartbeat(
            task.heartbeat_registry,
            task.heartbeat_stop,
            task.execution_id,
            task.lease_id,
        )
        if task.heartbeat_thread is not None:
            task.heartbeat_thread.join(timeout=2)
            task.heartbeat_thread = None
        self._status.finish(task.execution_id)
        # #551：每个上传任务一条 execution.reported（分段耗时 + 结局）。
        report_events.note_execution_reported(task, outcome)
        with self._lock:
            self._depth -= 1

    def _bulk_transfer(self, task: UploadTask) -> bool:
        """prepare + artifact 上传；True = 可进 report 车道，False = 中止（marker 保留）。"""
        if self._stop.is_set():
            return False  # never started; marker intact for the next startup
        self._status.set_phase(task.execution_id, "uploading")
        job_dir = task.execution_dir / "job"
        metadata, archive, outputs = prepare_or_failed(task)
        report_events.mark(task, "prepare_done")
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
                        # direct refs are dicts, CAS refs are strings (union).
                        ref: dict[str, Any] | str | None = upload_artifact_direct(
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
        report_events.mark(task, "bulk_done")
        return True

    def _report(self, task: UploadTask) -> str:
        metadata = task.prepared_metadata or {}
        archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
        # Quiesce the heartbeat before the final report: a beat racing the
        # commit loses the row lock and logs a spurious "lost ownership" 409.
        # Resume only while a transient report failure backs off.
        # Deliberately NOT run_with_retry: each backoff window must re-arm the
        # lease heartbeat, which the shared plain-sleep loop cannot express.
        upload_heartbeat.quiesce_task_heartbeat(task, _HEARTBEAT_JOIN_SECONDS)
        # #551：归档随 report 成功后的目录清理删除——字节数先落进计时器
        # （execution.reported 在 finalize 才发射）。
        if task.report_timer is not None and archive.is_file():
            task.report_timer.archive_bytes = archive.stat().st_size
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
                    self._client, task, self._heartbeat_interval
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
            return "aborted"  # stopped before the report resolved; marker stays
        marker = task.execution_dir / PENDING_FILENAME
        marker.unlink(missing_ok=True)
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        return "delivered" if status_code == 204 else "rejected"

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
