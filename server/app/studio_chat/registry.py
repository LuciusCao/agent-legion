"""Instance-level ACP agent registry for Studio chat (phase 3 chunk 4).

Admins maintain the list of launchable ACP agents ({id, label, command,
args[]}) plus the API base URL the bundled MCP server should call back. The
document lives in ``global_settings`` under its own key (``studio_agents``)
rather than inside the monolithic ``instance`` settings document: that
document has whole-document replace semantics and an admin UI that rebuilds
the payload field-by-field, so a new nested block would be clobbered by any
unrelated settings save. Non-admin users only ever pick an agent id from this
list — arbitrary command lines never cross the API boundary (RCE guard).

Reads go to the DB per use (like the skill-source store), so registry edits
take effect without a restart.
"""

from __future__ import annotations

import json
from typing import Any, cast

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

GLOBAL_SETTINGS_KEY = "studio_agents"
DEFAULT_API_BASE = "http://127.0.0.1:8000"


def default_registry_document() -> dict[str, Any]:
    return {"api_base": DEFAULT_API_BASE, "agents": []}


class StudioAgentRegistryStore:
    """Read/write the ``studio_agents`` document in ``global_settings``."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._dsn = database_dsn

    def get(self) -> dict[str, Any]:
        """Return the effective document: stored values over code defaults."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (GLOBAL_SETTINGS_KEY,),
            ).fetchone()
        document = default_registry_document()
        if row is None:
            return document
        stored = cast(dict[str, Any], json.loads(str(row["value"])))
        if stored.get("api_base"):
            document["api_base"] = str(stored["api_base"])
        if isinstance(stored.get("agents"), list):
            document["agents"] = stored["agents"]
        return document

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

    def find_agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self.get()["agents"]:
            if agent.get("id") == agent_id:
                return cast(dict[str, Any], agent)
        return None
