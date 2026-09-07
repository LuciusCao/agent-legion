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
        self, table: str, column: str, key: str, limit: int
    ) -> list[dict[str, Any]]:
        """Read up to ``limit`` rows whose ``column`` still holds an absolute path.

        The selection itself is the rewrite's idempotency guard: a clean
        column returns no rows and the caller writes nothing.
        """
        with self.read() as conn:
            rows = conn.execute(
                f"select {key} as k, {column} as v from {table}"
                f" where {column} like %s order by {key} limit %s",
                ("/%", limit),
            ).fetchall()
        return [{"key": str(row["k"]), "value": str(row["v"])} for row in rows]

    def rewrite_path_rows(
        self, table: str, column: str, key: str, updates: list[tuple[str, str]]
    ) -> None:
        """Apply one chunk of (value, key) rewrites in a single transaction."""
        with self.write() as conn:
            for value, row_key in updates:
                conn.execute(
                    f"update {table} set {column}=%s where {key}=%s",
                    (value, row_key),
                )
