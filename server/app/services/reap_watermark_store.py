"""Persistent bundle-reap watermark for the Agent execution broker.

``reaper.reap_terminal_bundles`` keeps its incremental-scan cursor in
``broker._reap_watermark``. Without persistence every Host restart replays
the startup full scan over ALL terminal request rows (streamed, chunked, but
still a full-table pass) before incremental mode resumes — a fixed tax that
grows with campaign history (#357). The watermark is persisted here after
every completed reap pass and reloaded on the first pass of a new process,
so a restart only rescans the overlap window.

Fallback semantics: a missing, corrupt, or non-monotonic document returns
None and the caller falls back to the full first scan (unchanged #139
behavior) — reaping is idempotent, so correctness never depends on the
watermark. The stored value is the PRE-scan anchor (``now - overlap``), and
the incremental query's own ``>=`` lower bound re-applies the overlap, so a
restored pass rescans exactly the window a same-process pass would.

Concurrency: single-Host deployments have one writer. Multi-replica Hosts
would need an advisory lock (or rely on the idempotence of the reaper's
unlink calls and accept duplicate scans) — deletions are idempotent, so a
stale or racing watermark never deletes live data, only risks redundant
scans.

The watermark lives in ``global_settings`` under the ``reap_watermark`` key,
the established store for small host-level state documents. SQL lives in
the queries layer (``global_settings`` KV mixin, issue #281); this store
keeps the timestamp-parsing domain logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)

logger = logging.getLogger(__name__)

GLOBAL_SETTINGS_KEY = "reap_watermark"


class ReapWatermarkStore:
    """Read/write the bundle-reap watermark in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def load(self) -> datetime | None:
        """The persisted watermark, or None when absent/corrupt (full-scan fallback)."""
        try:
            document = self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY)
        except (OSError, ValueError):
            # #204: a corrupt or unreadable watermark document must never break
            # reaping — None routes the caller to the full first scan, the
            # pre-#357 behavior. Outcome space is only "which scan runs"; the
            # exception is logged for diagnosis.
            logger.warning(
                "reap watermark unreadable; falling back to full first scan", exc_info=True
            )
            return None
        raw = (document or {}).get("watermark")
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("reap watermark %r is not an ISO timestamp; full first scan", raw)
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def save(self, watermark: datetime) -> None:
        """Persist one watermark advance (whole-document semantics; single writer)."""
        document: dict[str, Any] = {"watermark": watermark.isoformat()}
        self._kv().put_global_settings_document(GLOBAL_SETTINGS_KEY, document)

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn
