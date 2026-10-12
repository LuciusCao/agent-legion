from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg

from server.app.services.token_usage_parse import TokenUsageSummary, parse_run_usage
from server.app.storage_paths import ManagedPathError, resolve_data_path

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


def parse_lease_run_usage(source: dict[str, Any], data_dir: Path) -> TokenUsageSummary | None:
    """Pure filesystem parse: resolve the run dir and scan events.jsonl (no DB access).

    The ``source`` payload comes from ``JobQueries.read_lease_token_usage_source``
    (its own read checkout); keeping this step connection-free is what makes the
    capture pipeline's two pool checkouts sequential instead of nested (#1239).
    """
    node_run = source["node_run"]
    try:
        run_dir = resolve_data_path(node_run["run_dir"], data_dir, allow_missing=False)
    except (ManagedPathError, FileNotFoundError):
        return None
    return parse_run_usage(run_dir, node_run, workspace_id=source["workspace_id"])


def capture_token_usage_after_lease_finish(
    job_db: JobQueries,
    lease_id: str,
    data_dir: Path,
) -> None:
    """Capture token usage for a finished lease: facade read → events parse → facade persist.

    Orchestration only — both database touches ride the JobQueries facade
    (BOUNDARY-DATA-001), and the two checkouts are strictly sequential
    (#1239): the read checkout is released before the events.jsonl parse, and
    the persist checkout opens only after parsing succeeds, so even a size-1
    pool (``AGENT_LEGION_DB_POOL_MAX_SIZE=1``) can serve both. The pre-#1239
    shape nested a write transaction on top of the caller's still-held read
    connection; the 10s pool timeout was then swallowed by the best-effort
    guard below, silently dropping the usage row on every lease finish.
    """
    try:
        source = job_db.read_lease_token_usage_source(lease_id)
        if source is None:
            return
        summary = parse_lease_run_usage(source, data_dir)
        if summary is None:
            return
        job_db.persist_lease_token_usage(summary)
    except (OSError, psycopg.Error):
        # #204: usage capture is best-effort telemetry on the completion
        # path — the lease's result is already committed, so a failed parse
        # (missing/unreadable events.jsonl) or persist (DB connectivity)
        # must not fail the completion that already succeeded. The debug
        # traceback names the lease whose usage row is missing; the run's
        # events stay on disk for a manual recount.
        logger.debug("Failed to capture token usage for lease %s", lease_id, exc_info=True)
