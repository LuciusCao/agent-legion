"""DB path-column hygiene (issue #37).

Path columns (``node_runs.log_path/run_dir/session_dir``, ``jobs.storage_dir``)
must store data-dir-relative paths only. Absolute rows are legacy from
bare-metal deployments: they break when the deployment shape changes
(Docker, another machine, another directory), because the stored path names
a location that only exists on the writer's host. Reads stay fail-closed via
``resolve_data_path`` and finishes heal rows via ``canonicalize_finish_paths``,
but runs that never finish keep absolute paths forever. This module surfaces
the remaining absolute rows at startup (so a shape change is noticed before
executions stall) and centralizes the "legacy absolute resolved" warning
emitted by ``storage_paths``.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

_LEGACY_MESSAGE = "Legacy absolute path stored; resolving relative to data_dir instead"


def warn_legacy_absolute() -> None:
    """A legacy absolute stored path was resolved: log plus deprecation warning."""
    logger.warning(_LEGACY_MESSAGE)
    warnings.warn(_LEGACY_MESSAGE, DeprecationWarning, stacklevel=3)


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
