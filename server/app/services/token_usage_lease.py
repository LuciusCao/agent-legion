from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from server.app.db import transaction
from server.app.db.connection import DatabaseConnection
from server.app.db.dialect import ConnectSource
from server.app.services.token_usage import persist_node_run_usage
from server.app.services.token_usage_parse import TokenUsageSummary, parse_run_usage
from server.app.storage_paths import ManagedPathError, resolve_data_path

logger = logging.getLogger(__name__)


def parse_token_usage_for_lease(
    conn: DatabaseConnection,
    lease_id: str,
    data_dir: Path,
) -> TokenUsageSummary | None:
    """Read-only: parse events.jsonl for a finished lease and return its summary.

    This intentionally does not write to the database so that callers can keep
    the parse step outside of a database write transaction.
    """
    lease = conn.execute(
        "select node_run_id, workspace_id from executor_leases where id=%s", (lease_id,)
    ).fetchone()
    if lease is None or not lease["node_run_id"]:
        return None
    node_run = conn.execute(
        "select * from node_runs where id=%s", (lease["node_run_id"],)
    ).fetchone()
    if node_run is None or not node_run["run_dir"]:
        return None
    try:
        run_dir = resolve_data_path(node_run["run_dir"], data_dir, allow_missing=False)
    except (ManagedPathError, FileNotFoundError):
        return None
    return parse_run_usage(run_dir, dict(node_run), workspace_id=lease["workspace_id"])


def capture_token_usage_after_lease_finish(
    dsn: ConnectSource,
    lease_id: str,
    data_dir: Path,
) -> None:
    """Capture token usage for a finished lease: parse on a short read checkout, then persist on a separate write tx.

    The two pool checkouts are strictly sequential (#1239): the caller holds
    no connection across this call, so even a size-1 pool
    (``AGENT_LEGION_DB_POOL_MAX_SIZE=1``) can serve both. The previous shape
    took the caller's still-held read connection for the parse and opened
    the write transaction on top of it — a nested checkout that waited out
    the 10s pool timeout and let the best-effort guard below swallow
    PoolTimeout, silently dropping the usage row on every lease finish.
    """
    try:
        with transaction.read_connection(dsn) as read_conn:
            summary = parse_token_usage_for_lease(read_conn, lease_id, data_dir)
        if summary is None:
            return
        with transaction.write_transaction(dsn) as write_conn:
            persist_node_run_usage(write_conn, summary)
    except (OSError, psycopg.Error):
        # #204: usage capture is best-effort telemetry on the completion
        # path — the lease's result is already committed, so a failed parse
        # (missing/unreadable events.jsonl) or persist (DB connectivity)
        # must not fail the completion that already succeeded. The debug
        # traceback names the lease whose usage row is missing; the run's
        # events stay on disk for a manual recount.
        logger.debug("Failed to capture token usage for lease %s", lease_id, exc_info=True)
