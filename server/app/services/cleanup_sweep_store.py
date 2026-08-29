"""Persistent scan cursor for the expired node-run cleanup sweep.

The sweep deletes only files — ``node_runs`` rows are never removed — so
without a cursor every hourly pass re-pages the full expired tail
(production: 1.35M rows at ~500 rows per chunk, i.e. a near-continuous
scan). The retention cutoff moves forward over time and newly expired
rows have monotonically increasing ``finished_at``, so a per-(status,
action) high-water mark lets each pass resume where the last one stopped.

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

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction

GLOBAL_SETTINGS_KEY = "cleanup_sweep"

_EMPTY_CURSOR = (datetime.min.replace(tzinfo=UTC), 0)


class CleanupSweepStore:
    """Read/write the sweep high-water marks in ``global_settings``.

    Keys are ``"<status>:<action>"`` (e.g. ``completed:log``): each artifact
    action advances independently because log and run-dir retentions differ.
    """

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def load(self, cursor_key: str) -> tuple[datetime, int]:
        """Return the persisted ``(finished_at, id)`` mark, or the scan start."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
        if row is None:
            return _EMPTY_CURSOR
        entry = json.loads(str(row["value"])).get(cursor_key) or {}
        finished_raw = entry.get("finished_at")
        if not finished_raw:
            return _EMPTY_CURSOR
        finished = datetime.fromisoformat(finished_raw)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        return finished, int(entry.get("id", 0))

    def save(self, cursor_key: str, finished_at: datetime, row_id: int) -> None:
        """Advance one cursor key's mark; read-modify-write keeps the other keys."""
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
            document = json.loads(str(row["value"])) if row is not None else {}
            document[cursor_key] = {"finished_at": finished_at.isoformat(), "id": row_id}
            conn.execute(
                """
                insert into global_settings(key, value) values (%s, %s)
                on conflict(key)
                do update set value=excluded.value, updated_at=current_timestamp
                """,
                (GLOBAL_SETTINGS_KEY, json.dumps(document)),
            )
