"""Path-hygiene row access on the JobQueries facade (BOUNDARY-DATA-001, #521).

The one-time legacy-absolute rewrite (``services/path_hygiene``) reads
chunked absolute-path rows and rewrites them one by one; the SQL lives
here with the rest of the queries layer so the service file keeps its
(2, 0, 0) service-data-boundary baseline (the startup report's count
queries above it).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# (table, column, key) — the DB path columns that may hold legacy absolute
# rows, in rewrite order. Mirrors the columns counted by
# services.path_hygiene.count_absolute_db_paths.
PATH_HYGIENE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("node_runs", "log_path", "id"),
    ("node_runs", "run_dir", "id"),
    ("node_runs", "session_dir", "id"),
    ("jobs", "storage_dir", "id"),
)


class PathHygieneQueriesMixin(ConnectionQueriesMixin):
    def fetch_absolute_path_chunk(
        self, table: str, column: str, key: str, limit: int, *, after: str | None = None
    ) -> list[dict[str, Any]]:
        """Read up to ``limit`` absolute-path rows with ``column`` values, in key order.

        The selection itself is the rewrite's idempotency guard: a clean
        column returns no rows and the caller writes nothing. ``after`` is
        the key cursor — a full chunk of unmappable rows must still advance
        the scan (codex review on #530), so the caller pages past scanned
        keys instead of re-reading the same block forever.
        """
        cursor_sql = f" and {key} > %s" if after is not None else ""
        params: tuple[Any, ...] = ("/%", limit) if after is None else ("/%", after, limit)
        with self.read() as conn:
            rows = conn.execute(
                f"select {key} as k, {column} as v from {table}"
                f" where {column} like %s{cursor_sql} order by {key} limit %s",
                params,
            ).fetchall()
        return [{"key": str(row["k"]), "value": str(row["v"])} for row in rows]

    def rewrite_path_rows(
        self, table: str, column: str, key: str, updates: list[tuple[str, str, str]]
    ) -> None:
        """Apply (new_value, key, old_value) rewrites in one transaction.

        The stored-value re-check (codex review on #530) makes each write
        conditional on the row still holding the snapshotted value: a row
        updated between read and write (lease finish canonicalizing it,
        cleanup emptying it) is left alone, never overwritten back.
        """
        with self.write() as conn:
            for value, row_key, old_value in updates:
                conn.execute(
                    f"update {table} set {column}=%s where {key}=%s and {column}=%s",
                    (value, row_key, old_value),
                )
