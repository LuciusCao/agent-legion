"""Failed-result metadata helpers for the upload bulk lane.

Split out of ``prepare.py`` for the file budget: the empty-archive writer,
the uniform failed-report payload (shared by prepare failures and CAS
4xx terminal states) and the exit-code verdict carry no event-scan /
archive-build logic of their own. #843 PR-2 adds the metadata-only archive
writer (the v2 recycle target: every reported archive must carry the
metadata being reported as its ``result.json`` member).
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.code_contract import RESULT_METADATA_MEMBER
from worker.upload.stderr_evidence import stderr_error_message

if TYPE_CHECKING:
    from worker.upload.queue import UploadTask

MAX_ERROR_MESSAGE_CHARS = 4000


def write_empty_archive(archive: Path) -> None:
    with tarfile.open(archive, "w:gz"):
        pass


def write_metadata_only_archive(archive: Path, metadata: dict[str, Any]) -> None:
    """覆写为仅含 result.json 成员的归档（v2 判败降级的回收目标）。

    413 / 换写拒写等「归档不可提交」臂把证据归档回收成本形态：判败
    metadata 随首成员交付（否则 Host 400 缺成员）、体积必然远低于
    ``agent_workers.max_archive_bytes`` 下限（1 KiB）——可提交性优先。
    """
    payload = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(RESULT_METADATA_MEMBER)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))


def failed_metadata(task: UploadTask, error_message: str) -> dict[str, Any]:
    # failed 上报的统一载荷（prepare 失败 / CAS 4xx 终态共用）。
    return {
        "status": "failed",
        "exit_code": 1,
        "error_message": error_message[:MAX_ERROR_MESSAGE_CHARS],
        "command": list(task.command),
        "output_artifacts": {},
    }


def exit_verdict(exit_code: int, failure: str | None, stderr_tail: bytes) -> tuple[str, str]:
    """(status, error_message) of a finished agent process. ``failure`` is the
    event-scan attribution the caller already gated (exit-0 model error or the
    #952 output-truncation attribution); it outranks the exit-code faces."""
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
