from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime


def _sqlite_timestamp(dt: datetime) -> str:
    """Return a UTC timestamp SQLite can compare reliably with current_timestamp."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.ProgrammingError):
        conn.execute("rollback")


def _sync_job_status(conn: sqlite3.Connection, job_id: str) -> None:
    non_terminal = conn.execute(
        """
        select 1 from job_nodes
        where job_id=? and status not in ('completed', 'failed')
        """,
        (job_id,),
    ).fetchone()
    if non_terminal is not None:
        return

    any_failed = conn.execute(
        "select 1 from job_nodes where job_id=? and status='failed'",
        (job_id,),
    ).fetchone()
    new_status = "failed" if any_failed is not None else "completed"
    conn.execute(
        "update jobs set status=?, updated_at=? where id=?",
        (new_status, _sqlite_timestamp(datetime.now(UTC)), job_id),
    )
