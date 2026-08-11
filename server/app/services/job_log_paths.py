from __future__ import annotations

import logging
from pathlib import Path

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_run_dir_probe import derive_run_dir_from_log_path
from server.app.settings import Settings
from server.app.storage_paths import (
    ManagedPathError,
    resolve_data_path,
)

logger = logging.getLogger(__name__)


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


def resolve_run_dir_fallback(
    log_path: Path, node_key: str, job_id: str, settings: Settings
) -> Path | None:
    return derive_run_dir_from_log_path(log_path, node_key, job_id, settings.jobs_dir)
