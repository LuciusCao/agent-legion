"""Result preparation for the upload queue bulk lane.

Split out of ``queue.py`` so the queue module stays within its size
budget: this is the "process" task path that scans events, builds the result
archive, and derives the report metadata before any byte leaves the Worker.

#843 v2（PR-2）：本模块构建的归档是 **body 归档**（产物 + run_dir /
node.log，不含元数据成员）——结果元数据整体（含产物清单）由
``worker/upload/result_manifest.finalize_result_metadata`` 在 bulk 车道
终点（产物引用终态后）写成保留首成员 ``result.json``（UTF-8 JSON 文本，
shared/code_contract.RESULT_METADATA_MEMBER）。产物清单留在 result.json
里，不再产生 v1 换轨成员 ``result-output-artifacts.json``；v2 契约
（PR-1 评审 P3-2）明文禁止 payload 携带 ``output_artifacts_in_archive``
标记。大小治理同归档成员：``max_archive_bytes`` 是唯一大小门。
"""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from shared.output_truncation import OutputTruncation
from shared.pi_events import scan_and_compress_pi_events
from worker.state_evidence import dump_prep_evidence, prep_failure_message
from worker.upload.degraded_archive import (
    failed_metadata,
    write_degraded_empty_archive,
    write_empty_archive,
)
from worker.upload.report_policy import declared_ceiling_rejection
from worker.upload.result_metadata import MAX_ERROR_MESSAGE_CHARS, exit_verdict
from worker.upload.stderr_evidence import (
    AGENT_STDERR_FILENAME,
    secret_snapshot,
    stderr_tail_for_run,
)

if TYPE_CHECKING:
    from worker.upload.queue import UploadTask


def prepare_or_failed(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    # prepare_result + 失败降级为 failed 上报；直传回落后按清空的
    # artifact_uploads 重跑，tar 随之内嵌产物。#959：备妥的归档超 Host 下发
    # 上限即诚实判败（空归档 + failed），不把注定 413 的归档送进 report 车道。
    try:
        metadata, archive, outputs = prepare_result(task)
        rejection = declared_ceiling_rejection(task, archive)
    except Exception as exc:
        # #204 broad-except audit: 归档准备的故意降级（prepare_result 的
        # docstring 契约："may raise — caller degrades"，镜像拆分前的内联
        # catch-all）。逃逸族混族——events 扫描/压缩、tar 构建（OSError）、
        # code_runner 的归档路径、manifest 畸形——但结果必须永远可上报，
        # 否则执行会卡到租约过期被 Host 重调度。吞是对的：降级产物是空
        # result.tar.gz + failed_metadata，语义钉子即"准备失败 = run
        # failed"。日志保全：错误文本截断 4000 字符后随 error_message 报
        # 给 Host，随结果持久化、两侧可见。
        # #1147：清场前先把证据转储进 state 目录（work_root 之外）——运行
        # 目录被 agent 自删时 events.jsonl 随目录灭失，这里是最后的取证点。
        # 转储先于空归档落盘：execution_dir 整个消失时空归档写入自身会抛。
        # #1168 P1：空归档经 write_degraded_empty_archive 落盘——写前重建
        # 父目录、execution_dir 不可写时兜底 state 目录，失败臂的 I/O 不再
        # 逃出 except（逃出 = bulk 车道异常退出、failed 结果报不上）。
        evidence = dump_prep_evidence(task, exc)
        archive = write_degraded_empty_archive(task)
        return failed_metadata(task, prep_failure_message(task, exc, evidence)), archive, []
    if rejection is not None:
        return failed_metadata(task, rejection), archive, []
    return metadata, archive, outputs


def prepare_result(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    """Build (metadata, body archive, output names); may raise — caller
    degrades to a failed-result report, mirroring the old inline catch-all.

    #843 v2：返回的归档是 body 归档（产物 + run_dir，**无 result.json
    成员**)——元数据在 bulk 车道终点由 finalize_result_metadata 写入；
    中间归档永不外发（report 车道只见到 finalize 后的最终形态）。"""
    archive = task.execution_dir / "result.tar.gz"
    if task.kind == "prebuilt":
        metadata = dict(task.prebuilt_metadata or {})
        metadata.setdefault("output_artifacts", {})
        write_empty_archive(archive)
        return metadata, archive, []
    if task.exec_kind == "code":
        # 批次 2：code 归档（expected_outputs + 根部 node.log）与 metadata
        # 由 code_runner 负责（含 auth_failure_connection）。延迟导入：
        # code_runner 依赖 upload_queue.UploadTask，顶层导入会成环。
        from worker.result_archive import prepare_code_result

        return prepare_code_result(task)
    job_dir = task.execution_dir / "job"
    run_dir = job_dir / "runs" / task.node_key / "worker"
    events = run_dir / "events.jsonl"
    # Pi exits 0 even when the model call fails (e.g. provider 401); one
    # pass folds the model-error scan into the compression rewrite.
    # #748: the pass persists the non-JSON (merged-stderr) tail to the sink
    # file at scan time; stderr_tail_for_run reads it back when a re-entry
    # (direct-upload fallback / worker-restart restore) finds the events
    # file already compressed — a second scan would yield nothing.
    # The scan redacts before any cut or durable write — the stderr tail AND
    # the kept events' string values (#842: tool output echoing a secret must
    # not ride the compressed events.jsonl to the Host renderer) — through
    # ONE immutable registry snapshot (#844: the retired separate
    # secret_spans / max_secret_chars reads raced register_secrets).
    # 崩溃/超时（非 0 退出）下 model_error 归因让位给退出码归因——扫描
    # 结论只在 exit 0 时采纳。
    # #952: the same pass counts per-call output truncations (stopReason=length).
    snapshot = secret_snapshot()
    scanned_model_error, scanned_original, _, scanned_tail = scan_and_compress_pi_events(
        events,
        stderr_sink=run_dir / AGENT_STDERR_FILENAME,
        redactor=snapshot,
        event_observer=(truncation := OutputTruncation()).observe,
    )
    # #1165 belt-and-braces：带 redactor 的整趟扫描失败已在 pi_events 内把
    # 原文件就地截空（见其失败臂）；original==0 而文件仍在且非空 = 截断也
    # 失败（EACCES 族），此时绝不让未脱敏字节随 run_dir 进归档——诚实判败
    # 走空归档（#959 语义保持：failed 是可上报的降级，不是丢结果）。
    # original==0 的另外两形态（文件缺失 / 空文件）不触发本守卫。
    if scanned_original == 0 and events.is_file() and events.stat().st_size > 0:
        raise RuntimeError(f"pi events scan failed; the raw events file survived: {events}")
    stderr_tail = stderr_tail_for_run(run_dir, scanned_tail)
    outputs = [name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()]
    # #952: attribution only — replaces the opaque "Missing outputs" (exit 0,
    # Host-judged) / "Agent process exited 1" (velites output contract) face;
    # a truncated run whose outputs all landed still completes, and model
    # errors / budget exhaustion / contract violations / crashes / timeouts
    # keep their attribution (exclusion rules: OutputTruncation.failure).
    failure = truncation.failure(task.expected_outputs, outputs, task.exit_code) or (
        scanned_model_error if task.exit_code == 0 else None
    )
    result_status, error = exit_verdict(task.exit_code, failure, stderr_tail)
    metadata = {
        "status": result_status,
        "exit_code": task.exit_code,
        "error_message": error,
        "command": list(task.command),
        "output_artifacts": {},
        "run_dir": PurePosixPath(run_dir.relative_to(job_dir)).as_posix(),
    }
    # #748: agent_stderr_tail rides the report metadata (not just the
    # archive) so the DB row + external error_summary surface the crash
    # reason without unpacking the archive. Keep the END of the tail
    # (#755 终审 P2-2): the tail exists because the crash stack sits at the
    # end of the stream — a head cut would drop the crash header wholesale
    # whenever the tail is full. #755 终审 P3-1: 124 (timeout) carries the
    # tail too — the attribution face keeps "Agent process timed out", the
    # evidence face is decoupled from it.
    if task.exit_code not in (0, 130) and stderr_tail:
        metadata["agent_stderr_tail"] = stderr_tail.decode("utf-8", "replace")[
            -MAX_ERROR_MESSAGE_CHARS:
        ]
    # #160 D12：与 upload_queue._bulk_transfer 同一直传判定（#201 收敛进
    # UploadTask.is_direct_upload）；直传时产物不再内嵌归档（字节走 presigned PUT）。
    direct = task.is_direct_upload(outputs)
    with tarfile.open(archive, "w:gz") as tar:
        if not direct:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
        tar.add(run_dir, arcname=str(run_dir.relative_to(job_dir)))
    return metadata, archive, outputs
