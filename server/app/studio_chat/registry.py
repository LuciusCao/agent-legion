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
take effect without a restart. SQL lives in the queries layer
(``global_settings`` KV mixin, issue #281); this module keeps the
defaults-synthesis domain logic.
"""

from __future__ import annotations

import ipaddress
from typing import Any, cast
from urllib.parse import urlsplit

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)

GLOBAL_SETTINGS_KEY = "studio_agents"
DEFAULT_API_BASE = "http://127.0.0.1:8000"


def default_registry_document() -> dict[str, Any]:
    return {"api_base": DEFAULT_API_BASE, "agents": []}


def api_base_host_is_internal(api_base: str) -> bool:
    """Whether the api_base host keeps scoped tokens inside the network (#158).

    api_base is the egress target for the per-session scoped Bearer token; a
    host that is neither loopback nor a private address means the token
    leaves the machine, which the admin route warns about. Unresolvable
    hostnames cannot be classified locally and count as external.
    """
    host = (urlsplit(api_base).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class StudioAgentRegistryStore:
    """Read/write the ``studio_agents`` document in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get(self) -> dict[str, Any]:
        """Return the effective document: stored values over code defaults."""
        document = default_registry_document()
        stored = self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY)
        if stored is None:
            return document
        if stored.get("api_base"):
            document["api_base"] = str(stored["api_base"])
        if isinstance(stored.get("agents"), list):
            document["agents"] = stored["agents"]
        return document

    def put(self, document: dict[str, Any]) -> None:
        self._kv().put_global_settings_document(GLOBAL_SETTINGS_KEY, document)

    def find_agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self.get()["agents"]:
            if agent.get("id") == agent_id:
                return cast(dict[str, Any], agent)
        return None

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn
