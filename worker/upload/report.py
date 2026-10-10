"""Final result-report loop for one prepared upload task.

#843 v2（PR-2）：上报的元数据在结果归档的 ``result.json`` 首成员里（bulk
车道终点 finalize 写入；判败降级由 ReportDegradeGate 换写归档成员），
``client.report`` 只带 format 头 + 租约头——本循环不再触碰元数据序列化。

#1098：report 传输层单次尝试（``_request_with_retry`` max_attempts=1），
重试全部交给本循环——两次 report 尝试之间 resume 心跳 → 退避 → quiesce，
心跳空窗收敛到单次尝试时长，不再叠加内层 3×timeout + 退避（修复前
~360s > 90s 租约 TTL，租约被过期清扫、重报吃 409 丢结果）。

#959：Host 应答分级（204 / 409 终态 / 401 认证丢失处置 / 其余 4xx 判决
/ 5xx 与网络错误瞬时）与一次性诚实判败降级见 ``worker.upload.report_policy``。
"""

from __future__ import annotations

import shutil
import threading
from typing import Any

from worker.host.transfer import TransferOperations, TransferStopped
from worker.upload import heartbeat as upload_heartbeat
from worker.upload.cleanup import drop_marker
from worker.upload.control import CombinedStop
from worker.upload.report_policy import (
    AUTH_LOST_STATUS,
    ReportDegradeGate,
    is_transient_status,
    is_verdict_rejection,
)
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
    """Report once per attempt with lease-aware backoff; return the terminal
    queue outcome.

    #1098：瞬时失败持租约持续重试（resume → 退避 → quiesce，心跳在两次
    尝试之间跳动），由 204 / 409 / ownership_lost 终止。#1082：401 走
    认证丢失处置——保留 marker 放弃本 attempt（重注册后 restore 重投），
    不把认证故障伪装成 run 失败。"""
    archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
    upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
    if task.report_timer is not None and archive.is_file():
        task.report_timer.archive_bytes = archive.stat().st_size
    backoff = retry_base_seconds
    status_code, body, lost = 0, b"", False
    # #959：仅 4xx 判决走的一次性诚实判败降级闸（降级载荷挂回
    # task.prepared_metadata 并换写归档 result.json 成员）；瞬时失败与
    # 401 持租约持续重试/分流处置。
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
                    task.execution_id, task.lease_id, archive, stop=stop
                )
            else:
                status_code, body = client.report(task.execution_id, task.lease_id, archive)
            if is_transient_status(status_code):
                # 传输层已把 5xx 归一为 RuntimeError（#1098 起单次尝试）、
                # 4xx 原样透传；408/425/429 与非 TransferOperations 形态回传的
                # 5xx 同归瞬时臂（持续重试）。
                raise RuntimeError(f"HTTP {status_code}: {body[:200]!r}")
        except TransferStopped:
            if task.ownership_lost.is_set():
                lost = True
                break
            return "aborted"
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
        if status_code == AUTH_LOST_STATUS:
            # #1082：认证丢失处置——token 失效是 Worker 级事实（Host 全端点
            # 同一 token），不是本次 run 的失败：不降级、不伪报 failed。
            # marker 保留交重启 restore 重投（aborted 形态）；worker 级收口
            # 走既有链条（status sync 的 WorkerAuthError → exit 2 → supervisor
            # 重启 → 重新注册），重注册后由租约归属决定重报或 409 终态。
            print(f"result report auth lost for {task.execution_id}; marker kept", flush=True)
            return "aborted"
        if is_verdict_rejection(status_code):
            # #755 R10 P2 同形（评审 P3-1）：降级换写（全档解压 + 重新 gzip）
            # 是「心跳已 quiesce、判败重报未发出」窗口里的重活——大归档/慢
            # 存储上可超过租约 TTL，租约被过期清扫后重报吃 409、删 marker、
            # 整次执行重跑。重写期间重新武装心跳，完成后 quiesce 再重报；
            # 401/409/非判决不经此臂。
            task.heartbeat_thread = upload_heartbeat.resume_upload_heartbeat(
                client, task, heartbeat_interval
            )
            try:
                degraded = degrade_gate.on_rejection(status_code, rejection)
            finally:
                upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
            if degraded:
                # #959：409/401 之外的 4xx 是确定性判决——直接删 marker 会让
                # 租约过期后整次执行重跑、重跑再撞同一判决。降级一次为诚实
                # 判败上报（gate 已换写归档的 result.json 成员），Host 记录
                # 显式失败。
                continue
        break
    else:
        return "aborted"
    if status_code == 204:
        # #1174 二轮 P3-3：收尾形态随 drop_marker 传入（tombstone 记录
        # delivered/rejected/lost，滞留记录可区分「已交付」与「判决丢弃」）。
        drop_marker(task, "delivered")
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        return "delivered"
    outcome = "lost" if lost else "rejected"
    drop_marker(task, outcome)
    return outcome
