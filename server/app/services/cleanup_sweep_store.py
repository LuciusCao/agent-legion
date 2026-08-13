"""Persistent scan cursor for the expired node-run cleanup sweep.

The sweep deletes only files — ``node_runs`` rows are never removed — so
without a cursor every hourly pass re-pages the full expired tail
(production: 1.35M rows at ~500 rows per chunk, i.e. a near-continuous
scan). The retention cutoff moves forward over time and newly expired
rows have monotonically increasing ``finished_at``, so a per-status
high-water mark lets each pass resume where the last one stopped.

Trade-off: the cursor advances past every row a chunk returned, including
rows whose file deletion failed (logged as a warning at the time) — later
passes never retry them. Cleanup is best-effort; a permanently failed row
costs one stale file, not an hourly full-table rescan. Rows inserted late
with a ``finished_at`` below the cursor (e.g. backfilled history) are
likewise never swept.

The cursor lives in ``global_settings`` under the ``cleanup_sweep`` key,
the established store for small host-level state documents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

GLOBAL_SETTINGS_KEY = "cleanup_sweep"

_EMPTY_CURSOR = (datetime.min.replace(tzinfo=UTC), 0)


class CleanupSweepStore:
    """Read/write the per-status sweep high-water marks in ``global_settings``."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._dsn = database_dsn

    def load(self, status: str) -> tuple[datetime, int]:
        """Return the persisted ``(finished_at, id)`` mark, or the scan start."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
        if row is None:
            return _EMPTY_CURSOR
        entry = json.loads(str(row["value"])).get(status) or {}
        finished_raw = entry.get("finished_at")
        if not finished_raw:
            return _EMPTY_CURSOR
        finished = datetime.fromisoformat(finished_raw)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        return finished, int(entry.get("id", 0))

    def save(self, status: str, finished_at: datetime, row_id: int) -> None:
        """Advance one status's mark; read-modify-write keeps the other status."""
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
            document = json.loads(str(row["value"])) if row is not None else {}
            document[status] = {"finished_at": finished_at.isoformat(), "id": row_id}
            conn.execute(
                """
                insert into global_settings(key, value) values (%s, %s)
                on conflict(key)
                do update set value=excluded.value, updated_at=current_timestamp
                """,
                (GLOBAL_SETTINGS_KEY, json.dumps(document)),
            )
