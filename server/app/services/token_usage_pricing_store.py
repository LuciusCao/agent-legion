"""Database-backed storage for the global ``token_usage`` pricing config.

Pricing is a product setting: it lives only in the ``global_settings`` table
and is edited through the admin API; no yaml fallback exists. The stored
document has the shape ``{currency, pricing: [...]}`` so
``token_usage_pricing.calculate_cost`` consumes it unchanged.

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

GLOBAL_SETTINGS_KEY = "token_usage"


class TokenUsagePricingStore:
    """Read/write the token_usage pricing document in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get(self) -> dict[str, Any] | None:
        """Return the stored pricing document, or None when unset."""
        return self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY)

    def put(self, document: dict[str, Any]) -> None:
        self._kv().put_global_settings_document(GLOBAL_SETTINGS_KEY, document)

    def effective_config(self, base_config: dict[str, Any]) -> dict[str, Any]:
        """Return ``base_config`` with its token_usage section set from the DB.

        When no document is stored the section is dropped entirely, so cost
        calculation reports ``pricing_missing`` instead of falling back to
        yaml.
        """
        effective = dict(base_config)
        document = self.get()
        if document is None:
            effective.pop("token_usage", None)
        else:
            effective["token_usage"] = document
        return effective

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn
