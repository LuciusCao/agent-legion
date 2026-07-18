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


def find_extra_run_dirs(data_dir: Path, job_dir: Path, node_key: str) -> list[tuple[Path, str]]:
    """Return run dirs older than the newest for a node as (path, data-relative) pairs."""
    run_parent = job_dir / "runs" / node_key
    if not run_parent.is_dir():
        return []
    token_dirs = [d for d in run_parent.iterdir() if d.is_dir()]
    if len(token_dirs) <= 1:
        return []
    token_dirs.sort(key=_birthtime, reverse=True)
    extra: list[tuple[Path, str]] = []
    for old in token_dirs[1:]:
        try:
            extra.append((old, make_data_relative(old, data_dir)))
        except Exception as exc:
            logger.warning("Failed to remove extra run dir %s: %s", old, exc)
    return extra


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
    removed = 0
    for old, old_rel in find_extra_run_dirs(data_dir, job_dir, node_key):
        try:
            remove_path(old)
            conn.execute(
                "update node_runs set run_dir = '', session_dir = '' where run_dir = ?",
                (old_rel,),
            )
            removed += 1
        except Exception as exc:
            logger.warning("Failed to remove extra run dir %s: %s", old, exc)
    return removed
