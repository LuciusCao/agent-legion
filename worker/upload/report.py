"""Final result-report loop for one prepared upload task."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from worker.host.transfer import (
    HostRequestError,
    ResultHeaderOverflow,
    TransferOperations,
    TransferStopped,
)
from worker.upload import heartbeat as upload_heartbeat
from worker.upload.cleanup import drop_marker
from worker.upload.control import CombinedStop
from worker.upload.prepare import failed_metadata, prepare_or_failed
from worker.upload.task import UploadTask

# #748 R4：换轨预检的体积上限。Host 对超 max_archive_bytes 的 report body
# 直接 413（server/app/routes/agent_workers.py）；该值是 Host 实例设置，
# 无任何下发通道（register/claim/config 面均无此字段），worker 只能取对齐
# server 默认（server/app/executor_runtime.py 的 64 MiB）的保守常量——
# Host 调大上限时此处需同步。保守方向：宁可拒绝换轨本地判败，也绝不把
# 大产出人群重内嵌送进必死 413（丢结果 → 租约过期 → 全量重跑）。
_ARCHIVE_EMBED_CEILING_BYTES = 64 * 1024 * 1024


def _embedded_artifacts_bytes(task: UploadTask) -> float:
    """换轨预检：归档内嵌会打包进 tar 的产物字节总量（未压缩口径——gzip
    对二进制不可假设，run_dir 的 events/日志有运行时限额，产物载荷占主导）。
    与 prepare_result 同源地遍历 expected_outputs；stat 失败按 +inf——大小
    未知即拒绝换轨。"""
    job_dir = task.execution_dir / "job"
    total = 0.0
    for name in task.expected_outputs:
        try:
            total += (job_dir / PurePosixPath(name)).stat().st_size
        except OSError:
            return float("inf")
    return total


def report_task(
    client: Any,
    task: UploadTask,
    shutdown: threading.Event,
    heartbeat_interval: float,
    *,
    retry_base_seconds: float,
    retry_cap_seconds: float,
    heartbeat_join_seconds: float,
    upload_cas_artifact: Callable[[Path], str | None],
) -> str:
    """Report once with lease-aware backoff; return the terminal queue outcome.

    ``upload_cas_artifact`` is the queue's retrying CAS upload (None =
    stopped); the #748 R3 header-overflow fallback re-uploads the artifact
    bytes through it after re-preparing in archive-embed mode."""
    metadata = task.prepared_metadata or {}
    archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
    upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
    if task.report_timer is not None and archive.is_file():
        task.report_timer.archive_bytes = archive.stat().st_size
    backoff = retry_base_seconds
    status_code, body, lost = 0, b"", False
    # #748 R3：头溢出回退只走一次（序列化侧按 ref 形态判信号，CAS 形态
    # 不再抛；此处的一次性闸是双保险）。
    overflow_fallback = False
    while not shutdown.is_set():
        if task.ownership_lost.is_set():
            lost = True
            print(
                f"result report abandoned for {task.execution_id}: lease lost; discarding result",
                flush=True,
            )
            break
        stop = CombinedStop(shutdown, task.ownership_lost)
        try:
            if isinstance(client, TransferOperations):
                status_code, body = client.report(
                    task.execution_id, task.lease_id, metadata, archive, stop=stop
                )
            else:
                status_code, body = client.report(
                    task.execution_id, task.lease_id, metadata, archive
                )
        except TransferStopped:
            if task.ownership_lost.is_set():
                lost = True
                break
            return "aborted"
        except ResultHeaderOverflow as exc:
            # #748 R3（codex review P1）：头预算咬到产物清单本身——直传
            # ref 形态（128 条 ~25KB）必须整体换轨而非截断前缀（Host 不
            # 用截断标记恢复引用，前缀之外的产物进不了 job_dir，成功的
            # 执行会被改判 Missing outputs）。复用 DirectUploadError 的
            # 回退链：清空直传规格重跑 prepare（tar 内嵌产物、引用回到
            # CAS 形态 ~78B/条，天然落预算），CAS 通道重新上传后再上报。
            # 只回退一次：重备后的引用是 CAS 形态（~78B/条，128 条 ~12KB
            # 天然落预算），序列化侧对 CAS 形态不再抛信号、直接走最后
            # 手段截断——本闸 + 形态判定双保险，不会死循环。
            if overflow_fallback:
                raise
            embedded_bytes = _embedded_artifacts_bytes(task)
            if embedded_bytes > _ARCHIVE_EMBED_CEILING_BYTES:
                # #748 R4（#755 换轨预检）：产物总量超归档内嵌上限时**不换轨**
                # ——重内嵌只会把大产出人群送进 Host 413 → 丢结果 → 租约过期
                # 全量重跑（每轮同样 413）。本地诚实判败：复用 CAS 4xx 判败
                # 先例的 failed_metadata；归档保持直传形态（产物字节本就不在
                # tar 里，events/日志照常携带），错误信息如实说明拒绝原因。
                detail = (
                    "an expected output could not be stat'ed"
                    if embedded_bytes == float("inf")
                    else f"output artifacts total {int(embedded_bytes)} bytes"
                )
                print(
                    f"result header overflow for {task.execution_id}: {detail};"
                    f" archive-embed fallback rejected by the size pre-check: {exc}",
                    flush=True,
                )
                overflow_fallback = True
                metadata = failed_metadata(
                    task,
                    f"{detail}: output artifacts exceed the archive-embed ceiling"
                    f" ({_ARCHIVE_EMBED_CEILING_BYTES} bytes); cannot switch to"
                    f" the archive-embedded channel",
                )
                task.prepared_metadata = metadata
                continue
            print(
                f"result header overflow for {task.execution_id}:"
                f" falling back to archive-embedded artifacts: {exc}",
                flush=True,
            )
            overflow_fallback = True
            task.artifact_uploads = {}
            fallback_metadata, fallback_archive, outputs = prepare_or_failed(task)
            # 换轨上传期间心跳必须重新武装（report 车道进入前已 quiesce，
            # CAS 通道的重试上传可能超出租约 TTL——同 RuntimeError 退避
            # 窗口的处理）。
            task.heartbeat_thread = upload_heartbeat.resume_upload_heartbeat(
                client, task, heartbeat_interval
            )
            try:
                try:
                    for name in outputs:
                        ref = upload_cas_artifact(task.execution_dir / "job" / PurePosixPath(name))
                        if ref is None:
                            # #755 对抗复审 P3-1：换轨中途判死与 bulk 车道同
                            # 纪律（queue._bulk_transfer：lost if ownership
                            # lost else aborted）——走 lost 终态由下方统一出
                            # 口 drop_marker + 按归属清目录，不滞留到重启。
                            if task.ownership_lost.is_set():
                                lost = True
                                break
                            return "aborted"  # shutting down; marker stays
                        fallback_metadata.setdefault("output_artifacts", {})[name] = ref
                except HostRequestError as upload_exc:
                    # CAS 4xx 终态：同 bulk 车道的语义——判 run failed 上报
                    # （归档保持换轨重备的形态），而非无限重试一个不会变的
                    # verdict。
                    fallback_metadata = failed_metadata(task, str(upload_exc))
            finally:
                upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
            metadata, archive = fallback_metadata, fallback_archive
            task.prepared_metadata = metadata
            task.prepared_archive = archive
            continue
        except RuntimeError as exc:
            print(f"result report retry for {task.execution_id}: {exc}", flush=True)
            task.heartbeat_thread = upload_heartbeat.resume_upload_heartbeat(
                client, task, heartbeat_interval
            )
            stop.wait(backoff)
            backoff = min(backoff * 2, retry_cap_seconds)
            upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
            continue
        if status_code == 204:
            break
        print(
            f"result report rejected for {task.execution_id}: HTTP {status_code}: {body[:200]!r}",
            flush=True,
        )
        break
    else:
        return "aborted"
    if status_code == 204:
        drop_marker(task)
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        return "delivered"
    drop_marker(task)
    return "lost" if lost else "rejected"
