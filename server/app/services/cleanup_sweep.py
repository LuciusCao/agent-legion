"""Batched maintenance sweeps for node-run artifacts.

Both sweeps share the same discipline at ~79k-job scale: filesystem
deletions never happen inside a database transaction, and the ``node_runs``
updates that accompany them are flushed in bounded batches inside short
transactions, so the hourly maintenance pass never holds a write lock while
it walks or deletes files.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.services.job_run_dir_lookup import derive_run_dir_from_index
from server.app.services.run_dir_cleanup import find_extra_run_dirs, remove_path
from server.app.storage_paths import resolve_data_path

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

RUN_DIR_UPDATE_BATCH_SIZE = 500
LOG_CLEANUP_CHUNK_SIZE = 500

_EXPIRED_NODE_RUNS_SQL = """
select id, job_id, node_key, log_path, run_dir, finished_at
from node_runs
where status = ?
  and finished_at is not null
  and finished_at < ?
  and (finished_at > ? or (finished_at = ? and id > ?))
order by finished_at, id
limit ?
"""


def _flush_run_dir_updates(db: JobQueries, pending: list[str]) -> None:
    """Apply buffered run_dir clears in one short transaction."""
    if not pending:
        return
    with db.connect() as conn:
        conn.executemany(
            "update node_runs set run_dir = '', session_dir = '' where run_dir = ?",
            [(old_rel,) for old_rel in pending],
        )
    pending.clear()


def cleanup_extra_runs_per_node(db: JobQueries, data_dir: Path) -> int:
    """Scan the filesystem and keep only the newest run dir per (job, node).

    This bounds disk usage even when retention windows are long: a retried
    node may produce many run directories, but only the latest one is useful
    after the node has finished. Directories are removed outside any
    transaction; the matching ``node_runs`` updates (served by
    ``idx_node_runs_run_dir``) are flushed every ``RUN_DIR_UPDATE_BATCH_SIZE``
    removals in a short transaction.
    """
    jobs_dir = data_dir / "jobs"
    if not jobs_dir.is_dir():
        return 0
    removed = 0
    pending: list[str] = []
    for workspace_dir in jobs_dir.iterdir():
        if not workspace_dir.is_dir():
            continue
        for job_dir in workspace_dir.iterdir():
            if not job_dir.is_dir():
                continue
            runs_dir = job_dir / "runs"
            if not runs_dir.is_dir():
                continue
            for node_dir in runs_dir.iterdir():
                if not node_dir.is_dir():
                    continue
                for old, old_rel in find_extra_run_dirs(data_dir, job_dir, node_dir.name):
                    remove_path(old)
                    pending.append(old_rel)
                    removed += 1
                    if len(pending) >= RUN_DIR_UPDATE_BATCH_SIZE:
                        _flush_run_dir_updates(db, pending)
    _flush_run_dir_updates(db, pending)
    return removed


def _remove_row_artifacts(
    row,
    data_dir: Path,
    log_cutoff: datetime,
    run_dir_cutoff: datetime,
    job_dir_index: dict[str, Path],
) -> tuple[int, int]:
    """Remove the log file and run dir for one expired row, if past retention."""
    try:
        raw_finished = row["finished_at"]
        finished = (
            raw_finished
            if isinstance(raw_finished, datetime)
            else datetime.fromisoformat(raw_finished)
        )
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
    except ValueError:
        return 0, 0
    logs_removed = 0
    run_dirs_removed = 0
    log_path_str = row["log_path"]
    if log_path_str and finished <= log_cutoff:
        try:
            remove_path(resolve_data_path(log_path_str, data_dir, allow_missing=True))
            logs_removed += 1
        except Exception as exc:
            logger.warning("Failed to remove log %s: %s", log_path_str, exc)
    run_dir_str = row["run_dir"]
    if finished <= run_dir_cutoff:
        if not run_dir_str:
            run_dir = derive_run_dir_from_index(row["job_id"], row["node_key"], job_dir_index)
            if run_dir is not None:
                run_dir_str = str(run_dir)
        if run_dir_str:
            try:
                remove_path(resolve_data_path(run_dir_str, data_dir, allow_missing=True))
                run_dirs_removed += 1
            except Exception as exc:
                logger.warning("Failed to remove run_dir %s: %s", run_dir_str, exc)
    return logs_removed, run_dirs_removed


def sweep_expired_node_runs(
    db: JobQueries,
    data_dir: Path,
    log_cutoff: datetime,
    run_dir_cutoff: datetime,
    job_dir_index: dict[str, Path],
) -> tuple[int, int]:
    """Remove expired node-run log files and run directories in bounded chunks.

    Rows are paged per terminal status with a ``(finished_at, id)`` keyset so
    ``idx_node_runs_status_finished_at`` serves every chunk read without
    sorting. Each chunk is fetched on its own short-lived read connection, so
    no transaction is held while files are deleted. The SQL cutoff is a
    coarse superset filter; the exact per-row retention check in
    ``_remove_row_artifacts`` is unchanged.
    """
    cutoff = max(log_cutoff, run_dir_cutoff)
    logs_removed = 0
    run_dirs_removed = 0
    for status in ("completed", "failed"):
        last_finished_at = datetime.min.replace(tzinfo=UTC)
        last_id = 0
        while True:
            with db._connect_read() as conn:
                rows = conn.execute(
                    _EXPIRED_NODE_RUNS_SQL,
                    (
                        status,
                        cutoff,
                        last_finished_at,
                        last_finished_at,
                        last_id,
                        LOG_CLEANUP_CHUNK_SIZE,
                    ),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                logs, run_dirs = _remove_row_artifacts(
                    row, data_dir, log_cutoff, run_dir_cutoff, job_dir_index
                )
                logs_removed += logs
                run_dirs_removed += run_dirs
            raw_last_finished_at = rows[-1]["finished_at"]
            last_finished_at = (
                raw_last_finished_at
                if isinstance(raw_last_finished_at, datetime)
                else datetime.fromisoformat(raw_last_finished_at).replace(tzinfo=UTC)
            )
            last_id = rows[-1]["id"]
            if len(rows) < LOG_CLEANUP_CHUNK_SIZE:
                break
    return logs_removed, run_dirs_removed
