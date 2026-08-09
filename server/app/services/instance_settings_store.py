"""Database-backed storage for the instance-level settings document.

Instance-level tunables (cleanup/monitoring policy, lease/heartbeat/sweeper
timing, agent worker limits, workflows feature gate) are product settings:
they live only in the ``global_settings`` table under the ``instance`` key
and are edited through the admin API; no yaml fallback exists
(config/app.yaml and the workflow.yaml runtime sections are retired).
"""

from __future__ import annotations

import json
from typing import Any, cast

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

GLOBAL_SETTINGS_KEY = "instance"


class InstanceSettingsStore:
    """Read/write the ``instance`` settings document in ``global_settings``."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._dsn = database_dsn

    def get(self) -> dict[str, Any] | None:
        """Return the stored instance document, or None when unset."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
        if row is None:
            return None
        return cast(dict[str, Any], json.loads(str(row["value"])))

    def put(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document)
        with write_transaction(self._dsn) as conn:
            conn.execute(
                """
                insert into global_settings(key, value) values (%s, %s)
                on conflict(key)
                do update set value=excluded.value, updated_at=current_timestamp
                """,
                (GLOBAL_SETTINGS_KEY, payload),
            )
