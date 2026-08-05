from __future__ import annotations

import logging
from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.db.transaction import write_transaction
from server.app.services.token_usage import (
    TokenUsageSummary,
    parse_run_usage,
    persist_node_run_usage,
)
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


def persist_token_usage_for_lease(
    conn: DatabaseConnection,
    lease_id: str,
    data_dir: Path,
) -> None:
    """Parse events.jsonl for a lease and persist the summary in one transaction."""
    summary = parse_token_usage_for_lease(conn, lease_id, data_dir)
    if summary is not None:
        persist_node_run_usage(conn, summary)


def capture_token_usage_after_lease_finish(
    conn: DatabaseConnection,
    lease_id: str,
    data_dir: Path,
) -> None:
    """Capture token usage for a finished lease; parse outside, persist inside a write tx."""
    try:
        summary = parse_token_usage_for_lease(conn, lease_id, data_dir)
        if summary is None:
            return
        # The caller hands us a read-only connection for the parse step; the
        # short persist transaction runs on its own connection to the same
        # PostgreSQL DSN via the unified write_transaction helper.
        with write_transaction(conn.database_dsn) as write_conn:
            persist_node_run_usage(write_conn, summary)
    except Exception:
        logger.debug("Failed to capture token usage for lease %s", lease_id, exc_info=True)
