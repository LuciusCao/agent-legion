"""Key-only reads on ``external_connections`` (issue #419).

The picker surface (run creation → ref items) needs the connection KEYS for
any signed-in user, while full views stay admin-only in
``server/app/routes/connections.py``. The read lives behind the JobQueries
facade (BOUNDARY-DATA-001): the key column is all this query selects, so
config/secret material cannot leak through it.
"""

from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Includes disabled connections: the picker is a reference list, and run
# creation fails fast on a disabled key either way.
_KEYS_SQL = "select key from external_connections order by key"


class ExternalConnectionKeyQueriesMixin(ConnectionQueriesMixin):
    """List external connection keys (key-only, no config material)."""

    def list_external_connection_keys(self) -> list[str]:
        """Sorted connection keys; the response body for GET /connections/keys."""
        with self._connect_read() as conn:
            rows = conn.execute(_KEYS_SQL).fetchall()
        return [str(row["key"]) for row in rows]
