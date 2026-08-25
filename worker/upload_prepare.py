"""Result preparation for the upload queue bulk lane.

Split out of ``upload_queue.py`` so the queue module stays within its size
budget: this is the "process" task path that scans events, builds the result
archive, and derives the report metadata before any byte leaves the Worker.
"""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from shared.pi_events import (
    compress_pi_events,
    scan_and_compress_pi_events,
)

if TYPE_CHECKING:
    from worker.upload_queue import UploadTask


def write_empty_archive(archive: Path) -> None:
    with tarfile.open(archive, "w:gz"):
        pass


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
        from worker.code_runner import prepare_code_result

        return prepare_code_result(task)
    job_dir = task.execution_dir / "job"
    run_dir = job_dir / "runs" / task.node_key / "worker"
    events = run_dir / "events.jsonl"
    # Pi exits 0 even when the model call fails (e.g. provider 401); one
    # pass folds the model-error scan into the compression rewrite.
    if task.exit_code == 0:
        model_error, _, _ = scan_and_compress_pi_events(events)
    else:
        model_error = None
        compress_pi_events(events)
    outputs = [name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()]
    if task.exit_code == 130:
        result_status, error = "cancelled", "Agent Worker is shutting down"
    elif task.exit_code == 0:
        if model_error:
            result_status, error = "failed", model_error
        else:
            result_status, error = "completed", ""
    else:
        result_status, error = "failed", f"Agent process exited {task.exit_code}"
    metadata: dict[str, Any] = {
        "status": result_status,
        "exit_code": task.exit_code,
        "error_message": error,
        "command": list(task.command),
        "output_artifacts": {},
        "run_dir": PurePosixPath(run_dir.relative_to(job_dir)).as_posix(),
    }
    # #160 D12：与 upload_queue._bulk_transfer 同一直传判定（每个产出都有
    # 上传规格才走直传）；直传时产物不再内嵌归档（字节走 presigned PUT）。
    direct = bool(task.artifact_uploads) and all(name in task.artifact_uploads for name in outputs)
    with tarfile.open(archive, "w:gz") as tar:
        if not direct:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
        tar.add(run_dir, arcname=str(run_dir.relative_to(job_dir)))
    return metadata, archive, outputs
