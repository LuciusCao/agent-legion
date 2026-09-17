"""Result preparation for the upload queue bulk lane.

Split out of ``queue.py`` so the queue module stays within its size
budget: this is the "process" task path that scans events, builds the result
archive, and derives the report metadata before any byte leaves the Worker.
"""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from shared.pi_events import scan_and_compress_pi_events
from worker.upload.result_metadata import (
    MAX_ERROR_MESSAGE_CHARS,
    failed_metadata,
    write_empty_archive,
)

if TYPE_CHECKING:
    from worker.upload.queue import UploadTask

# #748: run-dir member carrying the retained agent-stderr tail (see
# pi_events.STDERR_TAIL_BYTES). The whole run dir ships in the archive, so
# the Host-side job dir keeps the evidence beside the promoted events.jsonl.
AGENT_STDERR_FILENAME = "agent-stderr.log"


def _stderr_error_message(exit_code: int, stderr_tail: bytes) -> str:
    """#748: error_message for a crashed agent process — exit code plus the
    retained stderr tail's LAST line (the crash header: a panic/trace ends
    the stream, so the newest — and most explanatory — line is the last one;
    the external API's error_summary truncates at 240 chars). The full
    multi-line tail rides the archive member + metadata; the empty tail
    keeps the legacy message unchanged."""
    summary = stderr_tail.decode("utf-8", "replace").strip()
    if not summary:
        return f"Agent process exited {exit_code}"
    last_line = " ".join(summary.splitlines()[-1].split())
    return f"Agent process exited {exit_code}: {last_line[:200]}"


def prepare_or_failed(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    # prepare_result + 失败降级为 failed 上报；直传回落后按清空的
    # artifact_uploads 重跑，tar 随之内嵌产物。
    try:
        return prepare_result(task)
    except Exception as exc:
        # #204 broad-except audit: 归档准备的故意降级（prepare_result 的
        # docstring 契约："may raise — caller degrades"，镜像拆分前的内联
        # catch-all）。逃逸族混族——events 扫描/压缩、tar 构建（OSError）、
        # code_runner 的归档路径、manifest 畸形——但结果必须永远可上报，
        # 否则执行会卡到租约过期被 Host 重调度。吞是对的：降级产物是空
        # result.tar.gz + failed_metadata，语义钉子即"准备失败 = run
        # failed"。日志保全：错误文本截断 4000 字符后随 error_message 报
        # 给 Host，随结果持久化、两侧可见。
        archive = task.execution_dir / "result.tar.gz"
        write_empty_archive(archive)
        return failed_metadata(task, f"result preparation failed: {exc}"), archive, []


def prepare_result(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    """Build (metadata, archive, output names); may raise — caller degrades
    to a failed-result report, mirroring the old inline catch-all."""
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
    # #748: every path captures the non-JSON (merged-stderr) tail — the
    # compression rewrite is what destroys it, so this is the last chance.
    if task.exit_code == 0:
        model_error, _, _, stderr_tail = scan_and_compress_pi_events(events)
    else:
        model_error = None
        _, _, _, stderr_tail = scan_and_compress_pi_events(events)
    if stderr_tail:
        # 落盘在压缩 rewrite 之后（scan 已把 events.jsonl 原地收紧），文件
        # 本身随 tar.add(run_dir) 进归档、随 run_dir 被 Host 提升。
        (run_dir / AGENT_STDERR_FILENAME).write_bytes(stderr_tail)
    outputs = [name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()]
    if task.exit_code == 130:
        result_status, error = "cancelled", "Agent Worker is shutting down"
    elif task.exit_code == 0:
        if model_error:
            result_status, error = "failed", model_error
        else:
            result_status, error = "completed", ""
    elif task.exit_code == 124:
        # Timeout kill (synthetic 124 from wait_for_exit): stderr at this
        # point is partial-run noise, not a crash cause — keep the
        # established timeout attribution (#609) untouched.
        result_status, error = "failed", "Agent process timed out"
    else:
        result_status, error = "failed", _stderr_error_message(task.exit_code, stderr_tail)
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
    # reason without unpacking the archive.
    if task.exit_code not in (0, 130, 124) and stderr_tail:
        metadata["agent_stderr_tail"] = stderr_tail.decode("utf-8", "replace")[
            :MAX_ERROR_MESSAGE_CHARS
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
