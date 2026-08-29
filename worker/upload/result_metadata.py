"""Failed-result metadata helpers for the upload bulk lane.

Split out of ``prepare.py`` for the file budget: the empty-archive writer
and the uniform failed-report payload (shared by prepare failures and CAS
4xx terminal states) carry no event-scan/archive-build logic of their own.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from worker.upload.queue import UploadTask

MAX_ERROR_MESSAGE_CHARS = 4000


def write_empty_archive(archive: Path) -> None:
    with tarfile.open(archive, "w:gz"):
        pass


def failed_metadata(task: UploadTask, error_message: str) -> dict[str, Any]:
    # failed 上报的统一载荷（prepare 失败 / CAS 4xx 终态共用）。
    return {
        "status": "failed",
        "exit_code": 1,
        "error_message": error_message[:MAX_ERROR_MESSAGE_CHARS],
        "command": list(task.command),
        "output_artifacts": {},
    }
