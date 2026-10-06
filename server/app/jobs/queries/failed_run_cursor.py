"""Keyset cursor text for the failed-node-runs page (#713).

``"<finished_at>|<node_run_id>"`` with the timestamp in the job-list cursor
form (#891 validation reused); an empty timestamp half encodes a NULL
``finished_at`` (those rows sort first under ``finished_at desc``).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.failed_node_runs_sql import FailedRunCursor
from server.app.jobs.queries.job_pagination import parse_job_cursor

_CURSOR_ERROR = "cursor must be the next_cursor value from a previous page"


def parse_failed_run_cursor(cursor: str) -> FailedRunCursor:
    """Cursor text → keyset position; ``ValueError`` on any malformed form
    (same 422 contract as the job-list cursor) so SQL only binds parsed values."""
    finished_raw, sep, run_raw = cursor.partition("|")
    if not sep or not run_raw.isascii() or not run_raw.isdigit():
        raise ValueError(_CURSOR_ERROR)
    if not finished_raw:
        return None, int(run_raw)
    finished_at, _ = parse_job_cursor(cursor)
    return finished_at, int(run_raw)


def format_failed_run_cursor(row: dict[str, Any]) -> str:
    finished_at = row.get("finished_at")
    # Offset stripped like the job-list cursor: "+" in a URL query decodes
    # to a space; parse_job_cursor reads the naive value back as UTC.
    stamp = "" if finished_at is None else str(finished_at).removesuffix("+00:00")
    return f"{stamp}|{int(row['node_run_id'])}"
