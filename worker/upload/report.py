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
from worker.upload.embed_precheck import ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
from worker.upload.prepare import failed_metadata
from worker.upload.report_policy import (
    ReportDegradeGate,
    ensure_submittable_archive,
    is_transient_status,
)
from worker.upload.result_manifest import (
    ManifestEmbedExceedsArchiveCeiling,
    embed_output_artifacts_manifest,
)
from worker.upload.result_metadata import write_empty_archive
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
    只留 ``output_artifacts_in_archive`` 标记。

    #959：Host 应答分级（204 / 409 终态 / 其余 4xx 判决 / 5xx 与网络错误
    瞬时）与一次性诚实判败降级见 ``worker.upload.report_policy``。"""
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
    # #959：应答分级见 report_policy——仅 4xx 判决走的一次性诚实判败降级闸
    # （降级载荷挂回 task.prepared_metadata）；瞬时失败持租约持续重试。
    degrade_gate = ReportDegradeGate(task, archive)
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
            if is_transient_status(status_code):
                # 传输层已把 5xx 归一为 RuntimeError、4xx 原样透传；408/425/429
                # 与非 TransferOperations 形态回传的 5xx 同归瞬时臂（持续重试）。
                raise RuntimeError(f"HTTP {status_code}: {body[:200]!r}")
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
            # #755 codex R10 P2：embed（完整解压 + 重新 gzip）与判败臂的
            # 归档回收都是「心跳已暂停（进场 quiesce）、最终请求未发出」
            # 真空窗口里的重活——大归档/慢存储上可超过 Host 租约 TTL，
            # 租约被过期清扫后重报吃 409、删 marker、整次执行重跑。处置
            # 期间重新武装心跳，重写完成后再 quiesce 并发送最终请求——与
            # 下方 RuntimeError 退避臂的 resume/quiesce 同形。
            task.heartbeat_thread = upload_heartbeat.resume_upload_heartbeat(
                client, task, heartbeat_interval
            )
            try:
                # #755 codex R8 P2：embed 上限重校——原直传归档低于但接近
                # max_archive_bytes 时，新增清单成员会把它推过 Host 大小门禁
                # （重报必撞 413，而本循环把非 204 当终态删 marker，结果与
                # staging 登记全丢）。上限与换轨预检同源（claim 下发，未下发
                # 回落 64 MiB 默认）；embed 在原子替换前按 staging 实际大小
                # 拒写（原归档未动、证据保全），替换后的 re-stat 是兜底。
                ceiling = task.max_archive_bytes or ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
                try:
                    embed_output_artifacts_manifest(
                        archive,
                        metadata.get("output_artifacts", {}),
                        task.expected_outputs,
                        max_bytes=ceiling,
                    )
                except ManifestEmbedExceedsArchiveCeiling as too_large:
                    # 原归档未动（embed 替换前拒写）：带着完整证据走诚实判败
                    # （同 embed_switch_rejection 预检判败通道的形态）。
                    print(
                        f"result header overflow for {task.execution_id}: {too_large};"
                        f" reporting the run failed instead",
                        flush=True,
                    )
                    ensure_submittable_archive(archive, ceiling)
                    metadata = failed_metadata(task, str(too_large))
                    task.prepared_metadata = metadata
                    continue
                except (OSError, tarfile.TarError, ValueError) as embed_exc:
                    # embed 失败 = 清单无法随归档交付：诚实判败（同 prepare 预检
                    # 判败臂的形态）。embed 是原子替换，失败时原归档未动。
                    ensure_submittable_archive(archive, ceiling)
                    metadata = failed_metadata(
                        task, f"output artifacts manifest embed failed: {embed_exc}"
                    )
                    task.prepared_metadata = metadata
                    continue
                if archive.stat().st_size > ceiling:
                    # re-stat 兜底（embed 未带上限的调用形态）：大归档不可提交
                    # ——回收成空归档诚实判败，而不是重报吃 413 后删 marker。
                    write_empty_archive(archive)
                    metadata = failed_metadata(
                        task,
                        f"output artifacts manifest embed grew the result archive past"
                        f" the {ceiling}-byte Host archive ceiling; cannot deliver the"
                        f" direct-upload manifest",
                    )
                    task.prepared_metadata = metadata
                    task.prepared_archive = archive
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
            finally:
                # 重写窗口结束：恢复「report 在飞即最后存活证明」纪律，
                # 重报在 quiesce 状态下发出（避免心跳与 commit 竞出伪 409）。
                upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
            continue
        except RuntimeError as exc:
            # #959：瞬时失败从不判败——租约持有期间持续退避重试，由 204 /
            # 409 / ownership_lost 终止（重试幂等见 report_policy）。
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
        rejection = f"HTTP {status_code}: {body[:200]!r}"
        print(f"result report rejected for {task.execution_id}: {rejection}", flush=True)
        if degrade_gate.on_rejection(status_code, rejection):
            # #959：409 之外的 4xx 是确定性判决——直接删 marker 会让租约
            # 过期后整次执行重跑、重跑再撞同一判决。降级一次为诚实判败上报，
            # Host 记录显式失败。
            metadata = task.prepared_metadata or metadata
            continue
        break
    else:
        return "aborted"
    if status_code == 204:
        drop_marker(task)
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        return "delivered"
    drop_marker(task)
    return "lost" if lost else "rejected"
