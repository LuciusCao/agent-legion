"""Filesystem probes for a job's run-token directories.

Pi/agent artifacts live under ``jobs/<workspace>/<shard>/<job_id>/runs/<node_key>/<token>/``
(legacy layout: ``jobs/<workspace>/<job_id>/runs/...``). The authoritative job
location is the ``jobs.storage_dir`` column, so hot paths probe a handful of
known candidates via ``job_run_dir_candidates`` / ``finish_job_dir_candidates``;
``derive_run_dir_from_log_path`` (full-workspace scan) remains as the legacy
fallback for callers that do not know the job's workspace.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from server.app.jobs.storage_layout import resolve_job_dir_candidates
from server.app.storage_paths import ManagedPathError, resolve_job_dir


def job_run_dir_candidates(
    jobs_dir: Path, workspace_id: str, storage_dir: str, job_id: str
) -> tuple[Path, ...]:
    """Return the known on-disk candidates for a job's directory.

    Uses the authoritative ``jobs.storage_dir`` when present, plus the
    sharded/legacy probes for the workspace, so callers can probe a handful
    of paths instead of scanning every workspace under ``jobs_dir``.
    """
    candidates: list[Path] = []
    if not jobs_dir.is_dir():
        return ()
    if storage_dir:
        with suppress(ManagedPathError):
            candidates.append(resolve_job_dir({"storage_dir": storage_dir}, jobs_dir))
    if workspace_id:
        for candidate in resolve_job_dir_candidates(jobs_dir, workspace_id, job_id):
            if candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def finish_job_dir_candidates(
    data_dir: Path | None, node_run: Any, job_id: str
) -> tuple[Path, ...] | None:
    """Return probe candidates from a node_run/jobs join row, or ``None``.

    ``node_run`` must carry ``job_workspace_id`` / ``job_storage_dir`` from the
    jobs table. ``None`` means the jobs row is gone and there is no workspace
    to anchor on — callers should keep the legacy full-scan fallback.
    """
    if data_dir is None or node_run is None:
        return None
    workspace_id = node_run["job_workspace_id"] or ""
    storage_dir = node_run["job_storage_dir"] or ""
    if not (workspace_id or storage_dir):
        return None
    return job_run_dir_candidates(data_dir / "jobs", workspace_id, storage_dir, job_id)


def derive_run_dir_from_job_dirs(job_dirs: Iterable[Path], node_key: str) -> Path | None:
    """Return the newest run-token dir for ``node_key`` across known job dirs.

    A job dir may exist in both layouts (e.g. re-intake created the empty
    sharded dir while runs still live in the legacy one), so keep probing
    until a candidate actually holds run tokens for this node.
    """
    if not node_key:
        return None
    token_dirs: list[Path] = []
    for candidate in job_dirs:
        run_parent = candidate / "runs" / node_key
        if not run_parent.is_dir():
            continue
        token_dirs.extend(d for d in run_parent.iterdir() if d.is_dir())
    if not token_dirs:
        return None
    return max(token_dirs, key=lambda p: p.stat().st_mtime)


def derive_run_dir_from_log_path(
    log_path: str | Path,
    node_key: str,
    job_id: str,
    jobs_dir: Path,
) -> Path | None:
    """Find the Pi token directory from the legacy log file path.

    The log file lives at ``logs/jobs/<job_id>-<node_key>.log`` while run
    artifacts live under the job dir. When no authoritative job dir is known,
    scan every workspace under ``jobs_dir`` — probing the sharded path first,
    then the legacy flat one — and pick the most recently modified token
    directory. Prefer ``derive_run_dir_from_job_dirs`` with
    ``job_run_dir_candidates`` on hot paths.
    """
    if not log_path or not node_key or not job_id:
        return None
    if not jobs_dir.is_dir():
        return None

    candidates = [
        candidate
        for workspace_dir in jobs_dir.iterdir()
        if workspace_dir.is_dir()
        for candidate in resolve_job_dir_candidates(jobs_dir, workspace_dir.name, job_id)
    ]
    return derive_run_dir_from_job_dirs(candidates, node_key)
