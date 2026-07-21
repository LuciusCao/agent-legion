from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.services.token_usage import parse_run_usage, persist_node_run_usage


def capture_and_persist_token_usage(
    conn: DatabaseConnection,
    run_dir: Path,
    node_run: dict[str, Any],
    workspace_id: str = "",
) -> None:
    """Parse token usage for a finished run and persist it in the same transaction."""
    summary = parse_run_usage(run_dir, node_run, workspace_id=workspace_id or None)
    if summary is not None:
        persist_node_run_usage(conn, summary)


def capture_and_persist_token_usage_for_lease(
    conn: DatabaseConnection,
    lease: dict[str, Any],
    data_dir: Path,
) -> None:
    """Load the node run for a lease, resolve its run_dir, and persist token usage."""
    from server.app.services.token_usage_lease import persist_token_usage_for_lease

    persist_token_usage_for_lease(conn, lease["id"], data_dir)
