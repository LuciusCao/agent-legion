"""Database-backed storage for the global ``token_usage`` pricing config.

Pricing is a product setting: it lives only in the ``global_settings`` table
and is edited through the admin API; no yaml fallback exists. The stored
document has the shape ``{currency, pricing: [...]}`` so
``token_usage_pricing.calculate_cost`` consumes it unchanged.
"""

from __future__ import annotations

import json
from typing import Any, cast

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

GLOBAL_SETTINGS_KEY = "token_usage"


class TokenUsagePricingStore:
    """Read/write the token_usage pricing document in ``global_settings``."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._dsn = database_dsn

    def get(self) -> dict[str, Any] | None:
        """Return the stored pricing document, or None when unset."""
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
