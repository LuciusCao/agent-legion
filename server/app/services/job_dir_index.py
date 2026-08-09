"""Job directory lookup from the jobs table (no filesystem enumeration).

The authoritative location of a job directory is the ``jobs.storage_dir``
column, so both the sharded and the legacy flat layout resolve without
scanning workspace directories — this keeps maintenance sweeps cheap when a
workspace accumulates 100k+ job dirs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.storage_paths import ManagedPathError, resolve_data_path

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

_JOB_ID_BATCH_SIZE = 1000


def locate_job_dir(job_id: str, storage_dir: str, data_dir: Path) -> Path | None:
    """Resolve a jobs row to its on-disk directory, or None on bad paths.

    Empty ``storage_dir`` falls back to the legacy ``jobs/<job_id>``
    convention; paths failing managed-root resolution — or resolving outside
    the jobs root (e.g. a corrupted row pointing at ``packages/...``) — return
    None so one bad row cannot stall the sweep or turn it destructive.
    """
    jobs_dir = data_dir / "jobs"
    if not storage_dir:
        return jobs_dir / job_id
    try:
        resolved = resolve_data_path(storage_dir, data_dir, allow_missing=True)
    except ManagedPathError:
        return None
    resolved_jobs_dir = jobs_dir.resolve()
    if resolved == resolved_jobs_dir or not resolved.is_relative_to(resolved_jobs_dir):
        return None
    return resolved


def build_job_dir_index(db: JobQueries, data_dir: Path, job_ids: set[str]) -> dict[str, Path]:
    """Map job_id to its job directory from the jobs table, batched by id."""
    index: dict[str, Path] = {}
    if not job_ids:
        return index
    ordered = sorted(job_ids)
    for offset in range(0, len(ordered), _JOB_ID_BATCH_SIZE):
        batch = ordered[offset : offset + _JOB_ID_BATCH_SIZE]
        with db._connect_read() as conn:
            rows = conn.execute(
                "select id, storage_dir from jobs where id = any(%s)", (batch,)
            ).fetchall()
        for row in rows:
            job_dir = locate_job_dir(str(row["id"]), row["storage_dir"], data_dir)
            if job_dir is not None:
                index[str(row["id"])] = job_dir
    return index


def iter_job_dirs(
    db: JobQueries, data_dir: Path, *, page_size: int = 500
) -> Iterator[tuple[str, Path]]:
    """Yield ``(job_id, job_dir)`` for every jobs row, keyset-paginated.

    Each page is fetched on its own short-lived read connection, so no
    transaction is held while the caller performs filesystem operations.
    """
    last_id = ""
    while True:
        with db._connect_read() as conn:
            rows = conn.execute(
                "select id, storage_dir from jobs where id > %s order by id limit %s",
                (last_id, page_size),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            job_dir = locate_job_dir(str(row["id"]), row["storage_dir"], data_dir)
            if job_dir is not None:
                yield str(row["id"]), job_dir
        last_id = str(rows[-1]["id"])
        if len(rows) < page_size:
            return
