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
rewrite below retires the legacy rows themselves, so the dedupe set and
the warnings converge to empty on a migrated deployment.
"""

from __future__ import annotations

import logging
import threading
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

_LEGACY_MESSAGE = "Legacy absolute path stored; resolving relative to data_dir instead"

# Per-process dedupe of the legacy-absolute warning (#521): hot paths
# (result commit, claim, dashboard reads) resolve the same stored legacy
# path every request, and the un-deduped logger.warning emit was a
# per-request CPU/IO cost on the saturated single-process control plane.
# Python's default warning filter only dedupes the warnings.warn DISPLAY;
# the log emit fired every time. Keyed by the stored path string so tests
# with per-test tmp paths keep firing (pytest.warns compatibility), and so
# distinct legacy paths stay individually visible.
_legacy_absolute_seen: set[str] = set()
_legacy_absolute_lock = threading.Lock()


def warn_legacy_absolute(stored_path: str = "") -> None:
    """A legacy absolute stored path was resolved: log plus deprecation warning.

    ``stored_path`` keys the process-wide dedupe — the same stored path
    warns once per process, distinct paths each warn. The empty default
    keeps direct/legacy callers warning (never deduped together), though
    both ``storage_paths`` call sites pass the stored path.
    """
    with _legacy_absolute_lock:
        if stored_path and stored_path in _legacy_absolute_seen:
            return
        if stored_path:
            _legacy_absolute_seen.add(stored_path)
    logger.warning(_LEGACY_MESSAGE)
    warnings.warn(_LEGACY_MESSAGE, DeprecationWarning, stacklevel=3)


def reset_legacy_absolute_dedupe() -> None:
    """Clear the warn-dedupe set (test isolation between per-tmp-path cases)."""
    with _legacy_absolute_lock:
        _legacy_absolute_seen.clear()


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

    # The count queries seq-scan jobs/node_runs; at prod scale (issue #106)
    # that stalled lifespan startup for minutes, so the report must run off
    # the startup path. Failures are logged, never raised into the caller.
    def _run() -> None:
        try:
            report_absolute_db_paths(db)
        except Exception:
            # #204 broad-except audit: the report thread's life support. It
            # runs detached at startup purely to surface legacy absolute
            # path rows (issue #37) — a DB blip mid-scan must not leave an
            # unhandled-exception traceback on a daemon thread (noisy, and
            # in some setups fatal to the process). The report is advisory:
            # failing it changes no behavior. Traceback logged.
            logger.exception("path-hygiene startup report failed")

    threading.Thread(target=_run, name="path-hygiene-report", daemon=True).start()


# One-shot rewrite of legacy absolute path rows (#521 / #37). The rebase
# rule mirrors resolve_data_path's suffix mapping (storage_paths): an
# absolute path rebases only when some component equals the data dir name
# and is followed by a managed category; unmappable rows are left alone
# and stay visible in the startup report. Chunked one column at a time so
# a prod-scale table never holds one long-running transaction (the same
# "off the hot path, small transactions" discipline the report thread
# follows). Idempotent by construction: only rows still matching
# ``like '/%'`` are selected, so a clean database does no writes. The SQL
# lives on the JobQueries facade (BOUNDARY-DATA-001): the service keeps
# its (2, 0, 0) baseline with the startup report's count queries.
_MIGRATE_CHUNK_ROWS = 500
_MANAGED_CATEGORIES = ("videos", "jobs", "logs", "packages")


def _rebase_legacy_absolute(stored_path: str, data_dir_name: str) -> str | None:
    """Map one legacy absolute path to its data-dir-relative form, or None.

    Same suffix rule as ``resolve_data_path``: find a component equal to
    the data dir name that is followed by a managed category, and rebase
    onto everything from that category onward. A path whose absolute form
    already sits under a data dir of the same name maps to the same
    relative value, so both legacy flavors converge.
    """
    parts = Path(stored_path).parts
    for i, part in enumerate(parts):
        if part == data_dir_name and i + 1 < len(parts) and parts[i + 1] in _MANAGED_CATEGORIES:
            return "/".join(parts[i + 1 :])
    return None


def migrate_absolute_db_paths(db: JobQueries, data_dir: Path) -> dict[str, int]:
    """Rewrite legacy absolute path rows to data-dir-relative; returns per-column counts.

    Runs one small write transaction per chunk per column (via the facade's
    path-hygiene queries); a failure aborts the remaining chunks (the next
    startup retries from the surviving absolute rows — the selection itself
    is the idempotency guard) and the exception propagates to the
    background wrapper's catch-all.
    """
    from server.app.jobs.queries.path_hygiene import PATH_HYGIENE_COLUMNS

    data_dir_name = data_dir.resolve(strict=True).name
    migrated: dict[str, int] = {}
    for table, column, key in PATH_HYGIENE_COLUMNS:
        done = 0
        # Key cursor (codex review on #530): a full chunk with zero mappable
        # rows must still ADVANCE, or the loop re-reads the same block
        # forever (unmappable absolute rows never leave the selection and
        # would spin the startup thread on repeated scans). Rows come back
        # in key order, so the last key of each full chunk is the cursor;
        # unmapped rows are simply left behind for the startup report.
        cursor: str | None = None
        while True:
            rows = db.fetch_absolute_path_chunk(
                table, column, key, _MIGRATE_CHUNK_ROWS, after=cursor
            )
            if not rows:
                break
            # (relative, key, old_value): the write re-checks the stored
            # value (codex review on #530) so a row updated between the
            # read and this transaction — a lease finish canonicalizing it,
            # a cleanup thread emptying it — is never overwritten back.
            updates = []
            for row in rows:
                relative = _rebase_legacy_absolute(row["value"], data_dir_name)
                if relative is not None:
                    updates.append((relative, row["key"], row["value"]))
            if updates:
                db.rewrite_path_rows(table, column, key, updates)
                done += len(updates)
            if len(rows) < _MIGRATE_CHUNK_ROWS:
                break
            cursor = rows[-1]["key"]
        if done:
            migrated[f"{table}.{column}"] = done
    if migrated:
        logger.info(
            "path-hygiene one-time rewrite: legacy absolute rows rebased to data-dir-relative (%s)",
            ", ".join(f"{name}={count}" for name, count in sorted(migrated.items())),
        )
    return migrated


def migrate_absolute_db_paths_background(db: JobQueries, data_dir: Path) -> None:
    """Kick the one-time rewrite onto a daemon thread; never blocks readiness.

    Same shape and failure contract as the report thread above: the scan
    walks jobs/node_runs, so it must not sit on the startup path
    (issue #106); failures are logged, never raised into the caller, and
    the next startup retries whatever chunks did not land.
    """

    def _run() -> None:
        try:
            migrate_absolute_db_paths(db, data_dir)
        except Exception:
            # #204 broad-except audit: the rewrite thread's life support,
            # same contract as the report thread — runs detached at
            # startup, advisory-plus-optional: a DB blip mid-rewrite
            # leaves the surviving absolute rows readable exactly as
            # before (reads stay fail-closed via resolve_data_path), and
            # the next startup's selection re-picks them. No behavior
            # depends on the rewrite having finished. Traceback logged.
            logger.exception("path-hygiene one-time rewrite failed")

    threading.Thread(target=_run, name="path-hygiene-rewrite", daemon=True).start()
