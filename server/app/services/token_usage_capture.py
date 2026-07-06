from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from server.app.services.token_usage import parse_run_usage, persist_node_run_usage
from server.app.storage_paths import ManagedPathError, resolve_data_path


def capture_and_persist_token_usage(
    conn: sqlite3.Connection,
    run_dir: Path,
    node_run: dict[str, Any],
    workspace_id: str = "",
) -> None:
    """Parse token usage for a finished run and persist it in the same transaction."""
    summary = parse_run_usage(run_dir, node_run, workspace_id=workspace_id or None)
    if summary is not None:
        persist_node_run_usage(conn, summary)


def capture_and_persist_token_usage_for_lease(
    conn: sqlite3.Connection,
    lease: dict[str, Any],
    data_dir: Path,
) -> None:
    """Load the node run for a lease, resolve its run_dir, and persist token usage."""
    node_run = conn.execute(
        "select * from node_runs where id=?", (lease["node_run_id"],)
    ).fetchone()
    if node_run is None or not node_run["run_dir"]:
        return
    try:
        run_dir = resolve_data_path(node_run["run_dir"], data_dir, allow_missing=False)
    except (ManagedPathError, FileNotFoundError):
        return
    capture_and_persist_token_usage(
        conn, run_dir, dict(node_run), workspace_id=lease["workspace_id"]
    )
