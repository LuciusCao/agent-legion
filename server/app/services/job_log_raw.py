from __future__ import annotations

import logging
from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, JobServiceError, NotFoundError
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_data_path

logger = logging.getLogger(__name__)

# Raw log downloads are capped to prevent multi-megabyte responses from
# consuming worker memory and blocking the HTTP server.
MAX_RAW_LOG_BYTES = 5 * 1024 * 1024


class PayloadTooLargeError(JobServiceError):
    pass


def resolve_job_log_path(log_path: str, settings: Settings) -> Path:
    if not log_path:
        raise InvalidOperationError("Empty log path")
    try:
        path = resolve_data_path(log_path, settings.data_dir, allow_missing=True)
        allowed_roots = {
            (settings.logs_dir / "jobs").resolve(): None,
            settings.jobs_dir.resolve(): None,
        }
        if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
            raise ValueError("Path outside allowed log roots")
    except (ValueError, ManagedPathError) as exc:
        raise InvalidOperationError("Invalid log path") from exc
    return path


def resolve_run_dir(run_dir: str, settings: Settings) -> Path | None:
    if not run_dir:
        return None
    try:
        path = resolve_data_path(run_dir, settings.data_dir, allow_missing=True)
    except ManagedPathError as exc:
        logger.warning("Ignoring invalid run_dir %r: %s", run_dir, exc)
        return None
    if not path.is_dir():
        logger.warning("run_dir %r does not exist or is not a directory", run_dir)
        return None
    return path


def read_raw_log(job_id: str, run_id: int, job_db: JobQueries, settings: Settings) -> str:
    run = job_db.get_node_run(job_id, run_id)
    if run is None:
        raise NotFoundError("Run not found")
    log_path = run.get("log_path") or ""
    if not log_path:
        return ""
    path = resolve_job_log_path(log_path, settings)
    if not path.is_file():
        return ""
    size = path.stat().st_size
    if size > MAX_RAW_LOG_BYTES:
        raise PayloadTooLargeError(
            f"Raw log is {size} bytes, exceeding the {MAX_RAW_LOG_BYTES} byte limit"
        )
    return path.read_text(encoding="utf-8", errors="replace")
