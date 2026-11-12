"""Data migration applied alongside the idempotent DDL replay (v73)."""

from __future__ import annotations

from typing import Any

from server.app.db.migrations.run_job_status_counts_sql import (
    BACKFILL_SQL,
    RETENTION_INDEX_DDL,
    TRIGGER_DDL,
)


def migrate_run_job_status_counts(conn: Any) -> None:
    """Create the run counter trigger and backfill it from jobs (v73)."""
    conn.execute(TRIGGER_DDL)
    conn.execute(RETENTION_INDEX_DDL)
    conn.execute(BACKFILL_SQL)
