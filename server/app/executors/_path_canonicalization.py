from __future__ import annotations

import logging
from pathlib import Path

from server.app.executors.models import ExecutionResult
from server.app.storage_paths import (
    ManagedPathError,
    derive_run_dir_from_log_path,
    make_data_relative,
    resolve_data_path,
)

logger = logging.getLogger(__name__)


def canonicalize_data_path(path: str, data_dir: Path | None, expected_category: str) -> str:
    """Return a validated canonical path for a database path column."""
    if not path or data_dir is None:
        return path
    resolved = resolve_data_path(path, data_dir, allow_missing=True)
    relative = make_data_relative(resolved, data_dir)
    category = Path(relative).parts[0]
    if category != expected_category:
        raise ManagedPathError(
            f"Stored path starts with '{category}', expected '{expected_category}'",
            root_kind=expected_category,
        )
    return relative


def canonicalize_run_dir(
    result: ExecutionResult,
    data_dir: Path | None,
    log_path: str,
    node_key: str,
    job_id: str,
) -> str:
    """Return a canonical ``run_dir`` for a finished node run.

    If the executor result omitted ``run_dir``, derive it from the log path and
    the on-disk ``jobs/<workspace>/<job_id>/runs/<node_key>/<token>/`` layout.
    """
    run_dir = canonicalize_data_path(result.run_dir, data_dir, "jobs")
    if run_dir or data_dir is None:
        return run_dir

    derived = derive_run_dir_from_log_path(log_path, node_key, job_id, data_dir / "jobs")
    if derived is not None:
        logger.warning(
            "Executor returned empty run_dir for %s.%s; derived %s from filesystem",
            job_id,
            node_key,
            derived,
        )
        return canonicalize_data_path(str(derived), data_dir, "jobs")
    return ""


def canonicalize_finish_paths(
    result: ExecutionResult,
    data_dir: Path | None,
    stored_log_path: str,
    node_key: str,
    job_id: str,
) -> tuple[str, str, str]:
    """Return canonical ``(log_path, run_dir, session_dir)`` for a finished run."""
    log_path = canonicalize_data_path(result.log_path or stored_log_path, data_dir, "logs")
    run_dir = canonicalize_run_dir(result, data_dir, log_path, node_key, job_id)
    session_path = result.session_dir or result.session_reference
    session_dir = canonicalize_data_path(session_path, data_dir, "jobs")
    return log_path, run_dir, session_dir
