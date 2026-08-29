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

from server.app.services.cleanup_sweep_store import CleanupSweepStore
from server.app.services.job_dir_index import iter_job_dirs
from server.app.services.job_run_dir_lookup import derive_run_dir_from_index
from server.app.services.run_dir_cleanup import find_extra_run_dirs, remove_path
from server.app.storage_paths import ManagedPathError, resolve_data_path

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

RUN_DIR_UPDATE_BATCH_SIZE = 500
LOG_CLEANUP_CHUNK_SIZE = 500

_EXPIRED_NODE_RUNS_SQL = """
select id, job_id, node_key, log_path, run_dir, finished_at
from node_runs
where status = %s
  and finished_at is not null
  and finished_at < %s
  and (finished_at > %s or (finished_at = %s and id > %s))
order by finished_at, id
limit %s
"""

# Forced-index page read (issue #122): at prod scale one terminal status
# covers ~98% of node_runs, so the planner judged
# idx_node_runs_status_finished_at_id unselective and chose seq scan + sort
# of the whole expired tail PER PAGE (19min/page observed, crushing instance
# IO and stalling the dispatch loop). SET LOCAL scoped to the page's own
# transaction pins the index scan back; statement_timeout is the fail-fast
# net so any future pathological plan cancels instead of running unbounded.
_PAGE_INDEX_PIN_SQL = "set local enable_seqscan = off"
_PAGE_TIMEOUT_SQL = "set local statement_timeout = '30s'"


def _flush_run_dir_updates(db: JobQueries, pending: list[str]) -> None:
    """Apply buffered run_dir clears in one short transaction."""
    if not pending:
        return
    with db.connect() as conn:
        conn.executemany(
            "update node_runs set run_dir = '', session_dir = '' where run_dir = %s",
            [(old_rel,) for old_rel in pending],
        )
    pending.clear()


def cleanup_extra_runs_per_node(db: JobQueries, data_dir: Path) -> int:
    """Walk the jobs table and keep only the newest run dir per (job, node).

    Job directories are located via the ``jobs.storage_dir`` column (keyset
    pagination over the jobs primary key) instead of enumerating workspace
    directories, so the sweep stays cheap when a workspace accumulates 100k+
    job dirs and works transparently across the sharded and legacy flat
    layouts. This bounds disk usage even when retention windows are long: a
    retried node may produce many run directories, but only the latest one is
    useful after the node has finished. Directories are removed outside any
    transaction; the matching ``node_runs`` updates (served by
    ``idx_node_runs_run_dir``) are flushed every ``RUN_DIR_UPDATE_BATCH_SIZE``
    removals in a short transaction.
    """
    removed = 0
    pending: list[str] = []
    for _job_id, job_dir in iter_job_dirs(db, data_dir):
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
    action: str,
    cutoff: datetime,
    job_dir_index: dict[str, Path],
) -> int:
    """Remove one expired artifact (``log`` or ``run_dir``) for a row past retention."""
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
        return 0
    if finished > cutoff:
        return 0
    if action == "log":
        log_path_str = row["log_path"]
        if not log_path_str:
            return 0
        try:
            remove_path(resolve_data_path(log_path_str, data_dir, allow_missing=True))
            return 1
        except ManagedPathError as exc:
            # Expected per-row failure (#204): the stored log path cannot be
            # resolved inside data_dir (legacy absolute path with an
            # unrecognizable layout, or an empty/escaping value). One bad row
            # must not abort the sweep — the mark still advances past it
            # (best-effort contract, see the store docstring).
            # remove_path itself already contains the OSError net, so the
            # only escape route out of this try block is ManagedPathError.
            logger.warning("Skip log with unresolvable path %r: %s", log_path_str, exc)
            return 0
    run_dir_str = row["run_dir"]
    if not run_dir_str:
        run_dir = derive_run_dir_from_index(row["job_id"], row["node_key"], job_dir_index)
        if run_dir is not None:
            run_dir_str = str(run_dir)
    if not run_dir_str:
        return 0
    try:
        remove_path(resolve_data_path(run_dir_str, data_dir, allow_missing=True))
        return 1
    except ManagedPathError as exc:
        # Same per-row discipline as the log branch above (#204): a row whose
        # run_dir cannot be mapped inside data_dir is skipped (warning), the
        # sweep keeps walking. The high-water mark advances past it either way,
        # so a permanently unresolvable row warns once per pass, not once per
        # page, and never wedges the cursor.
        logger.warning("Skip run_dir with unresolvable path %r: %s", run_dir_str, exc)
        return 0


def sweep_expired_node_runs(
    db: JobQueries,
    data_dir: Path,
    log_cutoff: datetime,
    run_dir_cutoff: datetime,
    job_dir_index: dict[str, Path],
) -> tuple[int, int]:
    """Remove expired node-run log files and run directories in bounded chunks.

    Rows are paged per (terminal status, artifact action) with a
    ``(finished_at, id)`` keyset so ``idx_node_runs_status_finished_at_id``
    serves every chunk read without sorting; the index choice is enforced
    (``_PAGE_INDEX_PIN_SQL``), not left to the planner (issue #122). Each
    chunk is fetched on its own short-lived read connection, so no
    transaction is held while files are deleted. The SQL cutoff is a coarse
    superset filter; the exact per-row retention check in
    ``_remove_row_artifacts`` is unchanged.

    Rows are never deleted, so each pass starts from the persisted
    high-water mark (``CleanupSweepStore``) instead of re-paging the whole
    expired tail; the mark advances after every processed chunk, past rows
    whose deletion failed as well (best-effort, see the store docstring).
    Each action gets its own cursor: log and run-dir retentions differ (7d
    vs 3d by default), and a shared cursor would skip log deletion for rows
    the run-dir pass already advanced past.
    """
    sweep_store = CleanupSweepStore(db)
    totals = {"log": 0, "run_dir": 0}
    for status in ("completed", "failed"):
        for action, cutoff in (("log", log_cutoff), ("run_dir", run_dir_cutoff)):
            cursor_key = f"{status}:{action}"
            last_finished_at, last_id = sweep_store.load(cursor_key)
            while True:
                with db._connect_read() as conn:
                    conn.execute(_PAGE_INDEX_PIN_SQL)
                    conn.execute(_PAGE_TIMEOUT_SQL)
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
                    totals[action] += _remove_row_artifacts(
                        row, data_dir, action, cutoff, job_dir_index
                    )
                raw_last_finished_at = rows[-1]["finished_at"]
                last_finished_at = (
                    raw_last_finished_at
                    if isinstance(raw_last_finished_at, datetime)
                    else datetime.fromisoformat(raw_last_finished_at).replace(tzinfo=UTC)
                )
                last_id = rows[-1]["id"]
                sweep_store.save(cursor_key, last_finished_at, last_id)
                if len(rows) < LOG_CLEANUP_CHUNK_SIZE:
                    break
    return totals["log"], totals["run_dir"]
