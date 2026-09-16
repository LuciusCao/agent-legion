"""Schema v82: install non-blocking delta folds for job status counters.

v77's fixed order is only statement-local. A transaction that fires the
counter triggers more than once can still form a counter-row AB-BA ring with
another transaction. A blocking AFTER-trigger advisory gate merely moves the
cycle: the statement already owns its jobs-row locks before it waits.

v82 instead appends net changes to delta tables. Try-lock winners atomically
claim committed deltas with ``DELETE ... RETURNING`` and fold them into the
base counters; losers return without waiting. Readers sum base plus pending
deltas in one snapshot, which is exact on either side of a fold commit. The
SQL is kept beside this wrapper so the lock and trigger protocol can be read
as one declarative unit without exceeding the Python migration budget.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_MIGRATION_SQL = Path(__file__).with_suffix(".sql")


def migrate_job_status_counts_advisory_locks(conn: Any) -> None:
    """Install v82's delta tables, non-blocking folders, and triggers."""
    conn.execute(_MIGRATION_SQL.read_text(encoding="utf-8"))
