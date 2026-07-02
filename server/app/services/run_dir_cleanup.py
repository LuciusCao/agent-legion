from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from server.app.storage_paths import make_data_relative

logger = logging.getLogger(__name__)


def remove_path(path: Path) -> None:
    """Remove a file or directory, logging failures instead of raising."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
    except OSError as exc:
        logger.warning("Failed to remove %s: %s", path, exc)


def _birthtime(path: Path) -> float:
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_mtime)


def cleanup_extra_runs_for_node(
    conn: sqlite3.Connection,
    data_dir: Path,
    job_dir: Path,
    node_key: str,
) -> int:
    """Remove all but the newest run directory for a single (job, node).

    The database ``run_dir``/``session_dir`` columns for removed directories
    are cleared so stale records do not point to deleted paths.
    """
    run_parent = job_dir / "runs" / node_key
    if not run_parent.is_dir():
        return 0
    token_dirs = [d for d in run_parent.iterdir() if d.is_dir()]
    if len(token_dirs) <= 1:
        return 0
    token_dirs.sort(key=_birthtime, reverse=True)
    removed = 0
    for old in token_dirs[1:]:
        try:
            old_rel = make_data_relative(old, data_dir)
            remove_path(old)
            conn.execute(
                "update node_runs set run_dir = '', session_dir = '' where run_dir = ?",
                (old_rel,),
            )
            removed += 1
        except Exception as exc:
            logger.warning("Failed to remove extra run dir %s: %s", old, exc)
    return removed


def cleanup_extra_runs_per_node(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Scan the filesystem and keep only the newest run dir per (job, node).

    This bounds disk usage even when retention windows are long: a retried
    node may produce many run directories, but only the latest one is useful
    after the node has finished.
    """
    jobs_dir = data_dir / "jobs"
    if not jobs_dir.is_dir():
        return 0
    removed = 0
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
                removed += cleanup_extra_runs_for_node(conn, data_dir, job_dir, node_dir.name)
    return removed
