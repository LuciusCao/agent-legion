"""Sharded on-disk layout for job storage directories.

Job dirs live under ``jobs/<workspace>/<shard>/<job_id>`` where ``shard`` is a
deterministic 2-hex-char prefix derived from the job id. This bounds the
number of entries per directory (workspaces accumulate jobs unboundedly;
a flat ``jobs/<workspace>/<job_id>`` layout degrades filesystem metadata
operations at 100k+ jobs).

Legacy flat ``jobs/<workspace>/<job_id>`` directories stay readable: the
authoritative location is the ``jobs.storage_dir`` column (see
``resolve_job_dir`` in ``server/app/storage_paths.py``), and fallback
filesystem probes check the sharded path first, then the legacy one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def job_shard(job_id: str) -> str:
    """Deterministic 2-hex-char shard for a job id (sha1 prefix)."""
    return hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:2]


def job_storage_dir(jobs_dir: Path, workspace_id: str, job_id: str) -> Path:
    """New-layout job dir: ``jobs_dir/<workspace>/<shard>/<job_id>``."""
    return jobs_dir / workspace_id / job_shard(job_id) / job_id


def resolve_job_dir_candidates(jobs_dir: Path, workspace_id: str, job_id: str) -> tuple[Path, Path]:
    """Return ``(sharded path, legacy flat path)`` for fallback probing."""
    return (
        job_storage_dir(jobs_dir, workspace_id, job_id),
        jobs_dir / workspace_id / job_id,
    )
