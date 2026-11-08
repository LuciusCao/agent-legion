"""DB path-column hygiene (issue #37).

Path columns (``node_runs.log_path/run_dir/session_dir``, ``jobs.storage_dir``)
must store data-dir-relative paths only. Absolute rows are legacy from
bare-metal deployments: they break when the deployment shape changes
(Docker, another machine, another directory), because the stored path names
a location that only exists on the writer's host. Reads stay fail-closed via
``resolve_data_path`` and finishes heal rows via ``canonicalize_finish_paths``,
but runs that never finish keep absolute paths forever. This module surfaces
the remaining absolute rows at startup (so a shape change is noticed before
executions stall, on a background thread so readiness never waits on it) and
centralizes the "legacy absolute resolved" warning emitted by
``storage_paths`` — deduped per stored path (#521: the hot read paths
resolve the same legacy row every request). The startup-time one-time
rewrite retires the legacy rows themselves, so the dedupe set and
the warnings converge to empty on a migrated deployment.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.services.path_hygiene_rewrite import (
    _MIGRATE_CHUNK_ROWS,
)
from server.app.services.path_hygiene_rewrite import (
    migrate_absolute_db_paths as _migrate_absolute_db_paths,
)
from server.app.services.path_hygiene_warnings import (
    _LEGACY_WARN_DEDUPE_CAP,
)
from server.app.services.path_hygiene_warnings import (
    reset_legacy_absolute_dedupe as reset_legacy_absolute_dedupe,
)
from server.app.services.path_hygiene_warnings import (
    warn_legacy_absolute as _warn_legacy_absolute,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


def warn_legacy_absolute(stored_path: str = "") -> None:
    """Log plus deprecation warning, deduped by the stored path."""
    _warn_legacy_absolute(stored_path, dedupe_cap=_LEGACY_WARN_DEDUPE_CAP)


def count_absolute_db_paths(db: JobQueries) -> dict[str, int]:
    """Absolute-path row counts per DB path column (all zero when clean)."""
    with db.connect() as conn:
        node_runs = conn.execute(
            "select count(*) filter (where log_path like '/%') as log_path,"
            " count(*) filter (where run_dir like '/%') as run_dir,"
            " count(*) filter (where session_dir like '/%') as session_dir"
            " from node_runs"
        ).fetchone()
        jobs = conn.execute(
            "select count(*) filter (where storage_dir like '/%') as storage_dir from jobs"
        ).fetchone()
    runs_row = node_runs or {}
    jobs_row = jobs or {}
    counts = {key: int(runs_row.get(key, 0)) for key in ("log_path", "run_dir", "session_dir")}
    counts["jobs.storage_dir"] = int(jobs_row.get("storage_dir", 0))
    return counts


def report_absolute_db_paths(db: JobQueries) -> dict[str, int]:
    """Log a warning for every DB path column still holding absolute paths."""
    counts = count_absolute_db_paths(db)
    dirty = {name: count for name, count in counts.items() if count}
    if dirty:
        logger.warning(
            "DB path columns hold legacy absolute paths (breaks on deployment "
            "shape change, issue #37): %s",
            ", ".join(f"{name}={count}" for name, count in sorted(dirty.items())),
        )
    return counts


def report_absolute_db_paths_background(db: JobQueries) -> None:
    """Kick the startup report onto a daemon thread; never blocks readiness."""

    def _run() -> None:
        try:
            report_absolute_db_paths(db)
        except Exception:
            # #204 broad-except audit: detached advisory report; a DB failure
            # must not escape the thread or affect readiness. Traceback logged.
            logger.exception("path-hygiene startup report failed")

    threading.Thread(target=_run, name="path-hygiene-report", daemon=True).start()


def migrate_absolute_db_paths(db: JobQueries, data_dir: Path) -> dict[str, int]:
    """Rewrite legacy absolute path rows to data-dir-relative."""
    return _migrate_absolute_db_paths(db, data_dir, chunk_rows=_MIGRATE_CHUNK_ROWS)


def migrate_absolute_db_paths_background(db: JobQueries, data_dir: Path) -> None:
    """Kick the one-time rewrite onto a daemon thread; never blocks readiness."""

    def _run() -> None:
        try:
            migrate_absolute_db_paths(db, data_dir)
        except Exception:
            # #204 broad-except audit: detached optional rewrite; failed chunks
            # remain absolute and are retried on the next startup. Traceback logged.
            logger.exception("path-hygiene one-time rewrite failed")

    threading.Thread(target=_run, name="path-hygiene-rewrite", daemon=True).start()
