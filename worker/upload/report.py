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
