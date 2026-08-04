from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.job_log_paths import (
    resolve_job_log_path,
    resolve_run_dir,
    resolve_run_dir_fallback,
)
from server.app.settings import Settings

# Raw log downloads are capped to avoid blocking the HTTP server.
MAX_RAW_LOG_BYTES = 5 * 1024 * 1024


class PayloadTooLargeError(JobServiceError):
    pass


def _read_capped_text(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_RAW_LOG_BYTES:
        raise PayloadTooLargeError(
            f"Raw log is {size} bytes, exceeding the {MAX_RAW_LOG_BYTES} byte limit"
        )
    return path.read_text(encoding="utf-8", errors="replace")


def read_raw_log(job_id: str, run_id: int, job_db: JobQueries, settings: Settings) -> str:
    run = job_db.get_node_run(job_id, run_id)
    if run is None:
        raise NotFoundError("Run not found")
    log_path = run.get("log_path") or ""
    if not log_path:
        return ""
    path = resolve_job_log_path(log_path, settings)
    if path.is_file():
        return _read_capped_text(path)

    run_dir = resolve_run_dir(run.get("run_dir") or "", settings)
    if run_dir is None:
        run_dir = resolve_run_dir_fallback(
            path, run.get("node_key") or "", run.get("job_id") or "", settings
        )
    if run_dir is None:
        return ""

    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        return _read_capped_text(events_path)

    return ""
