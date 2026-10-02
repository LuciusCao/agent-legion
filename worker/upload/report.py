"""Final result-report loop for one prepared upload task."""

from __future__ import annotations

import shutil
import tarfile
import threading
from typing import Any

from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_FLAG
from worker.host.transfer import (
    ResultHeaderOverflow,
    TransferOperations,
    TransferStopped,
)
from worker.upload import heartbeat as upload_heartbeat
from worker.upload.cleanup import drop_marker
from worker.upload.control import CombinedStop
from worker.upload.prepare import failed_metadata
from worker.upload.result_manifest import embed_output_artifacts_manifest
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
) -> str:
    """Report once with lease-aware backoff; return the terminal queue outcome.

    #755 codex P1：结果头溢出（直传 ref 清单撞破头预算）的处置是「清单
    走归档成员」——产物字节已在 S3（presigned 通道，不重复传输），完整
    direct-ref 清单写成归档首成员 ``result-output-artifacts.json``，头里
    只留 ``output_artifacts_in_archive`` 标记。"""
    metadata = task.prepared_metadata or {}
    archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
    upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
    if task.report_timer is not None and archive.is_file():
        task.report_timer.archive_bytes = archive.stat().st_size
    backoff = retry_base_seconds
    status_code, body, lost = 0, b"", False
    # #748 R3：头溢出处置只走一次（序列化侧按 ref 形态判信号——标记臂已
    # 清空清单，重报不会再抛；此处的一次性闸是双保险）。
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
            # #755 codex P1：头预算咬到产物清单本身——直传 ref 形态（128 条
            # ~25KB）的旧回退是清规格重跑 prepare + legacy CAS 通道重传产物
            # （违反 EXEC-ARTIFACT-WORKER-001 的 presigned-only 约束，且字节
            # 双传）。新协议：产物字节不动（已在 S3），完整 direct-ref 清单
            # 作为归档首成员交付，头里只带 output_artifacts_in_archive 布尔
            # 标记，Host commit 层从归档读回清单并 enrich outcome。
            if overflow_fallback:
                raise
            overflow_fallback = True
            try:
                embed_output_artifacts_manifest(
                    archive, metadata.get("output_artifacts", {}), task.expected_outputs
                )
            except (OSError, tarfile.TarError, ValueError) as embed_exc:
                # embed 失败 = 清单无法随归档交付：诚实判败（同 prepare 预检
                # 判败臂的形态）。embed 是原子替换，失败时原归档未动。
                metadata = failed_metadata(
                    task, f"output artifacts manifest embed failed: {embed_exc}"
                )
                task.prepared_metadata = metadata
                continue
            print(
                f"result header overflow for {task.execution_id}:"
                f" output artifacts manifest embedded in the archive: {exc}",
                flush=True,
            )
            # #755 对抗复审 P3：embed 重写了归档，计时器的 archive_bytes
            # 过期——按 embed 后的真实大小刷新（纯观测面，但别让操作者
            # 看着 embed 前的尺寸排障）。
            if task.report_timer is not None and archive.is_file():
                task.report_timer.archive_bytes = archive.stat().st_size
            metadata = dict(metadata)
            metadata["output_artifacts"] = {}
            metadata[RESULT_OUTPUT_ARTIFACTS_FLAG] = True
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
