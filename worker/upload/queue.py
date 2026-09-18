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
failure backs off (worker/upload/heartbeat.py). #644: a beat-plane lost
verdict (409 family) or a 409 report answer is TERMINAL for the delivery —
the task's shared ``ownership_lost`` event stops the retry loop, the marker
is dropped and the execution dir is discarded via the #564 ownership check,
so a dead lease can neither spin unbounded report retries nor hammer the
Host with re-registered beats. A task condemned before bulk skips prepare
and transfer outright. The queue also owns a per-execution handoff barrier:
a re-claimed attempt condemns and fully drains the prior uploader before it
may reuse ``work_root/<execution_id>``. That barrier, rather than point-in-time
checks before individual file operations, is the filesystem safety boundary;
the checks remain for prompt cancellation and bounded lane occupancy.
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
from worker.host.transfer import HostRequestError, TransferOperations
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY
from worker.upload import heartbeat as upload_heartbeat
from worker.upload import report_events
from worker.upload.cleanup import drop_marker
from worker.upload.constants import PENDING_FILENAME as PENDING_FILENAME
from worker.upload.control import BulkOutcome, CombinedStop
from worker.upload.handoff import UploadHandoff
from worker.upload.report import report_task
from worker.upload.task import PendingUploadExists as PendingUploadExists
from worker.upload.task import UploadTask

if TYPE_CHECKING:
    from worker.execution.heartbeat_batch import BatchHeartbeatRegistry

# MAX_ERROR_MESSAGE_CHARS 的定义在 result_metadata（failed_metadata 的截断
# 上限）；execution_run 沿本模块导入，`as` 惯用法重导出而非再定义一份
# 副本（#200/#201 同族的 sync-by-comment 反模式）。failed_metadata 经
# prepare 重导出（prepare_or_failed 同车）。
from worker.upload.prepare import failed_metadata, prepare_or_failed
from worker.upload.result_metadata import (
    MAX_ERROR_MESSAGE_CHARS as MAX_ERROR_MESSAGE_CHARS,
)
from worker.upload.scheduler import LaneScheduler

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
        # 调用方未给 stop 时自建（单测路径）；生产由 worker 服务注入共享事件。
        self._stop = stop if stop is not None else threading.Event()
        self._scheduler = LaneScheduler(
            MAX_DYNAMIC_CONCURRENCY, max_concurrency, thread_name_prefix="agent-upload"
        )
        self._handoff = UploadHandoff()

    def set_heartbeat_registry(self, registry: BatchHeartbeatRegistry) -> None:
        """Attach the per-Worker batch heartbeat coordinator (#352), once,
        after construction; queued upload tasks register their leases with
        it instead of owning single-beat threads."""
        self._heartbeat_registry = registry

    @property
    def depth(self) -> int:
        """Tasks queued or in flight; the claim loop reads this for backpressure."""
        return self._handoff.depth

    def set_max_concurrency(self, value: int) -> None:
        """热更新上传并发：调大立即补位，调小不抢占、在途任务自然跑完。"""
        self._scheduler.set_limit(value)

    def submit(self, task: UploadTask) -> None:
        """Persist the pending marker, then queue the delivery.

        Marker first: a crash between the two loses at most the result (the
        Host requeues after the lease expires), never reports twice."""
        marker = task.execution_dir / PENDING_FILENAME
        self._handoff.begin(task)
        try:
            atomic_write(marker, json.dumps(task.to_json(), ensure_ascii=False))
            # upsert: 重启恢复的任务在 reporter 里尚无条目，积压期间也要以
            # queued_upload 可见。
            self._status.upsert_phase(task.execution_id, "queued_upload", **task.status_fields)
            task.report_timer = report_events.UploadReportTimer()
            self._scheduler.submit(lambda: self._deliver_bulk(task))
        except Exception:
            # #204 broad-except audit: handoff 注册后的同步提交事务横跨 JSON
            # 序列化、marker I/O、status reporter 与 scheduler，逃逸族混族；
            # 任一路径失败都尚未向调用方承诺入队，必须统一撤销本机 barrier，
            # marker 若已落盘则保留给重启 restore。裸 re-raise 保留原异常。
            self._handoff.cancel_begin(task)
            raise

    def wait_for_prior_upload(
        self,
        execution_id: str,
        lease_id: str,
        stop: threading.Event,
        ownership_lost: threading.Event | None = None,
    ) -> bool:
        """Fence execution-dir reuse behind the prior upload's full teardown.

        A Host requeue can send the same execution back to this Worker while
        the old attempt is still preparing/uploading its result. Both attempts
        otherwise address ``work_root/<execution_id>``. The new execution lane
        calls this method after registering its heartbeat but before touching
        that directory: a different-lease uploader is condemned, then the new
        lane waits until marker/directory cleanup and queue accounting finish.

        Returns False when Worker shutdown or the incoming lease's own lost
        verdict interrupts the wait.
        """
        return self._handoff.wait_for_prior(execution_id, lease_id, stop, ownership_lost)

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
            # #644 codex3 P1：判死任务在 bulk 入口终止（危害链与论证见
            # _condemned_before_bulk 的 docstring）——零压缩、零上传、零 report。
            if self._condemned_before_bulk(task):
                print(
                    f"upload task condemned {task.execution_id}: lease lost before bulk",
                    flush=True,
                )
                outcome: BulkOutcome = "lost"
            else:
                outcome = self._bulk_transfer(task)
        except Exception as exc:
            # #204 broad-except audit: bulk 车道任务的存活安全网。
            # _bulk_transfer 的已知失败族（DirectUploadError 回落、
            # HostRequestError 终态、传输重试、判死中止）都在内部处理，逃到
            # 这里的是意外路径——但车道函数跑在 LaneScheduler 的池线程里，
            # 未捕获异常会落进无人读取的 Future 而完全静默，这里既是安全
            # 网也是唯一日志点。吞是对的：ready=False 走 _finalize（心跳
            # quiesce、深度记账），pending marker 原样保留，下次启动
            # restore 重新投递。日志保全：print 记录 execution_id 与异常
            # （仅消息、无堆栈，见 #298 审计报告的观察项）。
            print(f"upload task crashed for {task.execution_id}: {exc}", flush=True)
            outcome = "lost" if task.ownership_lost.is_set() else "aborted"
        if outcome == "ready":
            try:
                # 心跳保持跳动直到 report 前才 quiesce：report 车道排队期间
                # 租约仍需 proof of life。
                self._scheduler.submit(lambda: self._deliver_report(task), priority=True)
                return
            except RuntimeError:
                pass  # 调度器已关停；marker 留给下次启动恢复
            outcome = "aborted"
        try:
            if outcome == "lost":
                drop_marker(task)
        except Exception as exc:
            # #204 broad-except audit: lost 已是终态，marker/目录清理失败不能
            # 卡死 handoff；记录失败并让 stale sweeper / 下一 attempt 收口。
            print(f"upload cleanup failed for {task.execution_id}: {exc}", flush=True)
        finally:
            self._finalize(task, outcome)

    def _deliver_report(self, task: UploadTask) -> None:
        """report 车道入口：quiesce 心跳 → report → 删 marker 清目录。"""
        report_events.mark(task, "report_start")
        outcome = "aborted"
        try:
            outcome = report_task(
                self._client,
                task,
                self._stop,
                self._heartbeat_interval,
                retry_base_seconds=_RETRY_BASE_SECONDS,
                retry_cap_seconds=_RETRY_CAP_SECONDS,
                heartbeat_join_seconds=_HEARTBEAT_JOIN_SECONDS,
            )
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
        if not self._handoff.start_finalize(task):
            return
        try:
            upload_heartbeat.prune_heartbeat(
                task.heartbeat_registry, task.heartbeat_stop, task.execution_id, task.lease_id
            )
            if task.heartbeat_thread is not None:
                task.heartbeat_thread.join(timeout=2)
                task.heartbeat_thread = None
            self._status.finish(task.execution_id)
            # #551：每个上传任务一条 execution.reported（分段耗时 + 结局）。
            report_events.note_execution_reported(task, outcome)
        finally:
            # delivery_done 最后置位：同 execution 的新 attempt 只有在 marker /
            # 目录收尾与全部记账都完成后才可重用 execution_dir。
            self._handoff.complete_finalize(task)

    def _upload_one_artifact(
        self, job_dir: Path, name: str, task: UploadTask, direct: bool
    ) -> dict[str, Any] | str | None:
        """Upload one output: presigned PUT (#160 D12, dict refs) or the
        retrying CAS channel (string refs); None = stopped (retry next
        startup). Terminal 4xx propagates (HostRequestError): the caller
        reports the run failed instead of retrying a verdict."""
        path = job_dir / PurePosixPath(name)
        stop = CombinedStop(self._stop, task.ownership_lost)
        if direct:
            return upload_artifact_direct(path, task.artifact_uploads[name], stop=stop)
        return self._upload_with_retry(path, task)

    def _bulk_transfer(self, task: UploadTask) -> BulkOutcome:
        """prepare + artifact 上传；只返回结局，finalize 由车道外层唯一持有。"""
        if self._stop.is_set():
            return "aborted"  # never started; marker intact for the next startup
        self._status.set_phase(task.execution_id, "uploading")
        job_dir = task.execution_dir / "job"
        metadata, archive, outputs = prepare_or_failed(task)
        report_events.mark(task, "prepare_done")
        if self._condemned_before_bulk(task):
            return "lost"
        # #160 D12：直传判定经 UploadTask.is_direct_upload（#201 单点收敛）；
        # 直传走 presigned PUT，否则整体回落旧通道（CAS POST + tar 内嵌，tar
        # 已在 prepare_result 按同一方法决定是否内嵌）。
        direct = task.is_direct_upload(outputs)
        while True:
            uploaded: dict[str, Any] = {}
            restart = False
            for name in outputs:
                # #644 codex4 P1：入口检查只覆盖 arm 时刻；在途上传/退避
                # 数秒到数分钟，期间 lease 可能过期且本 worker 已重新
                # claim（新 attempt 删建同一 execution_dir）——每个跨
                # attempt 的文件动作（上传 / 回落 prepare）前重验，判死
                # 即中止走终态收尾。
                if self._condemned_before_bulk(task):
                    return "lost"
                try:
                    ref = self._upload_one_artifact(job_dir, name, task, direct)
                except DirectUploadError as exc:
                    # 直传失败（4xx / 重试耗尽 / 规格畸形）不判 run failed：清掉
                    # 上传规格重跑 prepare（tar 自动内嵌产物），重启循环走无限
                    # 重试的 CAS 通道，与无规格任务同一语义。
                    print(f"direct upload failed for {task.execution_id}: {exc}", flush=True)
                    if self._condemned_before_bulk(task):
                        return "lost"
                    task.artifact_uploads = {}
                    metadata, archive, outputs = prepare_or_failed(task)
                    if self._condemned_before_bulk(task):
                        return "lost"
                    direct, restart = False, True
                    break
                except HostRequestError as exc:
                    if self._condemned_before_bulk(task):
                        return "lost"
                    # Terminal 4xx on the artifact itself: report the run failed
                    # instead of looping forever on a verdict that cannot change.
                    metadata = failed_metadata(task, str(exc))
                    uploaded = {}
                    break
                if ref is None:
                    return "lost" if task.ownership_lost.is_set() else "aborted"
                uploaded[name] = ref
            if not restart:
                break
        metadata["output_artifacts"] = uploaded
        task.prepared_metadata = metadata
        task.prepared_archive = archive
        report_events.mark(task, "bulk_done")
        return "ready"

    def _condemned_before_bulk(self, task: UploadTask) -> bool:
        """Fast cancellation check; directory safety comes from the handoff barrier.

        A lost lease is terminal and should stop prepare/transfer promptly. A
        new local attempt cannot rebuild the shared execution_dir until this
        task's ``delivery_done`` event, so correctness does not depend on an
        impossible-to-make-atomic sequence of check-then-open calls.
        """
        return task.ownership_lost.is_set()

    def _upload_with_retry(self, path: Path, task: UploadTask) -> str | None:
        """Upload one artifact; None = stopped (retry next startup); 4xx propagates."""

        def retry_log(exc: BaseException, _backoff: float) -> None:
            print(f"artifact upload retry for {path.name}: {exc}", flush=True)

        return run_with_retry(
            lambda: (
                self._client.upload_artifact(
                    path, stop=CombinedStop(self._stop, task.ownership_lost)
                )
                if isinstance(self._client, TransferOperations)
                else self._client.upload_artifact(path)
            ),
            retriable=(RuntimeError,),
            terminal=(HostRequestError,),
            base_seconds=_RETRY_BASE_SECONDS,
            cap_seconds=_RETRY_CAP_SECONDS,
            stop=CombinedStop(self._stop, task.ownership_lost),
            on_retry=retry_log,
        )
