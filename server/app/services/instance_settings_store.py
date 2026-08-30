"""Database-backed storage for the instance-level settings document.

Instance-level tunables (cleanup/monitoring policy, lease/heartbeat/sweeper
timing, agent worker limits, workflows feature gate, materials TTL) are
product settings: they live only in the ``global_settings`` table under the
``instance`` key and are edited through the admin API; no yaml fallback
exists (config/app.yaml and the workflow.yaml runtime sections are retired).
Most values hydrate into Settings at startup; ``materials_ttl_days`` is read
fresh from this document at material completion/sweep time instead.

SQL lives in the queries layer (``global_settings`` KV mixin, issue #281);
this store is the domain facade over one fixed key.
"""

from __future__ import annotations

from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)

GLOBAL_SETTINGS_KEY = "instance"


class InstanceSettingsStore:
    """Read/write the ``instance`` settings document in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get(self) -> dict[str, Any] | None:
        """Return the stored instance document, or None when unset."""
        return self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY)

    def put(self, document: dict[str, Any]) -> None:
        self._kv().put_global_settings_document(GLOBAL_SETTINGS_KEY, document)

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn
