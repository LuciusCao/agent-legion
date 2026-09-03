"""Key-only reads on ``external_connections`` (issue #419).

The picker surface (run creation → ref items) needs the connection KEYS for
any signed-in user, while full views stay admin-only in
``server/app/routes/connections.py``. The reads live behind the JobQueries
facade (BOUNDARY-DATA-001): the key/enabled columns are all these queries
select, so config/secret material cannot leak through them.
"""

from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Enabled connections only (#425 review): the picker offers keys a run can
# actually use, so a disabled connection — even the sole one — is never
# auto-selected into ConnectionKeyField, and RunService._ref_candidate
# rejects a disabled key at run creation (true fail-fast; the old claim
# that "run creation fails fast on a disabled key either way" only held at
# execution time, in connection_tokens). Admin views still list disabled
# connections (server/app/services/connections.py::list).
_KEYS_SQL = "select key from external_connections where enabled=1 order by key"

_ENABLED_SQL = "select enabled from external_connections where key=%s"


class ExternalConnectionKeyQueriesMixin(ConnectionQueriesMixin):
    """List external connection keys (key-only, no config material)."""

    def list_external_connection_keys(self) -> list[str]:
        """Sorted connection keys; the response body for GET /connections/keys."""
        with self._connect_read() as conn:
            rows = conn.execute(_KEYS_SQL).fetchall()
        return [str(row["key"]) for row in rows]

    def external_connection_enabled(self, key: str) -> bool | None:
        """Enabled state of one connection; ``None`` when the key is unknown."""

        # The fail-fast gate for ref items (#425 review): run creation must
        # distinguish "key does not exist" from "key exists but is disabled",
        # so existence and enabled state both come from this single
        # key-scoped read. Key/enabled only — no config material (the column
        # whitelist guarding ``list_external_connection_keys`` applies here
        # the same way).
        with self._connect_read() as conn:
            row = conn.execute(_ENABLED_SQL, (key,)).fetchone()
        if row is None:
            return None
        return bool(row["enabled"])
