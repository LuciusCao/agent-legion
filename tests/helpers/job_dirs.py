"""Helpers for building on-disk job directories in tests.

Tests use these instead of hand-concatenating ``jobs/<ws>/<job_id>`` so
fixtures follow the sharded layout (`storage_layout.job_shard`). Pass
``sharded=False`` to build the legacy flat layout for backward-compat cases.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs.storage_layout import job_shard


def job_storage_ref(workspace_id: str, job_id: str, *, sharded: bool = True) -> str:
    """Return the data-relative ``storage_dir`` value for a test job."""
    if sharded:
        return f"jobs/{workspace_id}/{job_shard(job_id)}/{job_id}"
    return f"jobs/{workspace_id}/{job_id}"


def make_job_dir(data_dir: Path, workspace_id: str, job_id: str, *, sharded: bool = True) -> Path:
    """Create and return the on-disk job directory for a test job."""
    path = data_dir / job_storage_ref(workspace_id, job_id, sharded=sharded)
    path.mkdir(parents=True, exist_ok=True)
    return path
