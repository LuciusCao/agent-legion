"""Exit-code verdicts and the shared error_message bound for the upload
bulk lane.

Split out of ``prepare.py`` for the file budget. This module keeps the
exit-code verdict face, the shared failed-result payload and the agent body
archive writer; the metadata-only/degraded archive writers live in
``worker/upload/degraded_archive.py``. Every reported archive carries its
metadata as the reserved ``result.json`` member, written later by the result
manifest lane.
"""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from worker.upload.stderr_evidence import stderr_error_message

MAX_ERROR_MESSAGE_CHARS = 4000


def failed_metadata(task: Any, error_message: str) -> dict[str, Any]:
    """failed 上报的统一载荷（prepare 失败 / CAS 4xx 终态 / report 降级共用）。"""
    return {
        "status": "failed",
        "exit_code": 1,
        "error_message": error_message[:MAX_ERROR_MESSAGE_CHARS],
        "command": list(task.command),
        "output_artifacts": {},
    }


def write_agent_body(
    archive: Path,
    job_dir: Path,
    evidence_dir: Path | None,
    outputs: list[str],
    direct: bool,
    run_dir: Path,
) -> None:
    """Write declared outputs and the selected evidence directory as a body archive."""
    with tarfile.open(archive, "w:gz") as tar:
        if not direct:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
        if evidence_dir is not None:
            tar.add(evidence_dir, arcname=str(run_dir.relative_to(job_dir)))


def exit_verdict(exit_code: int, failure: str | None, stderr_tail: bytes) -> tuple[str, str]:
    """(status, error_message) of a finished agent process. ``failure`` is the
    event-scan attribution the caller already gated (exit-0 model error,
    explicitly recognized Provider auth failures on exit 1, or the #952
    output-truncation attribution); it outranks the exit-code faces."""
    if exit_code == 130:
        return "cancelled", "Agent Worker is shutting down"
    if failure:
        return "failed", failure
    if exit_code == 0:
        return "completed", ""
    if exit_code == 124:
        # Timeout kill (synthetic 124 from wait_for_exit): the attribution
        # face (error_message) keeps the established timeout wording (#609)
        # untouched — but the EVIDENCE face (agent_stderr_tail) still
        # rides along (#755 终审 P3-1): attribution and evidence are
        # decoupled, the partial-run stderr stays available for diagnosis.
        return "failed", "Agent process timed out"
    return "failed", stderr_error_message(exit_code, stderr_tail)
