"""Execution-plane terminal-row retention sweep (issue #354).

``agent_execution_requests`` / ``executor_leases`` /
``node_run_token_usage`` only ever grew: terminal rows accumulated forever,
so a single large campaign left the tables (and their claim-path indexes)
permanently fat and every subsequent scan paid for the corpses. This module
owns the time-dimensional row removal, driven by the ``execution_retention_days``
instance setting (0 = disabled — the default; no row is removed unless an
operator explicitly turns it on).

Table order and per-table predicates follow the foreign keys (the detailed
rationale lives with the SQL in ``jobs/queries/execution_retention.py``):
terminal requests first, then non-active leases, then token usage of
finished runs. ``node_runs`` rows themselves are never removed by retention
(they carry the per-job audit trail; their fat artifacts are already reaped
by ``cleanup_sweep.py``, which also never removes rows).

Discipline copied from ``cleanup_sweep.py``: keyset pagination, one short
transaction per bounded batch (the SQL lives in the queries layer,
BOUNDARY-DATA-001), and a persisted high-water mark per cursor so an
interrupted pass resumes where it stopped instead of re-paging the whole
expired tail. The mark only advances after a batch commits, and the removal
predicate is idempotent (rows either match the cutoff or they do not), so a
crash mid-pass loses no work and repeats nothing. Shrinking the window later
re-pages from the stored mark only when the new cutoff reaches back past it
— otherwise the sweep restarts from the beginning of the expired tail, which
is correct (the predicate re-filters everything) and bounded by the tail.

Bloat note (acceptance #3, code-level argument): batches are bounded
(``BATCH_SIZE``) and each runs in its own transaction, so each batch is a
short heap-tuple mark with a small index-entry cleanup — no long-running
transaction pins a snapshot and holds vacuum back. The steady state after a
sweep is a table whose live tuple count tracks the retention window, and
``pg_stat`` bloat stays bounded because autovacuum sees many small
dead-tuple batches instead of one giant one (the same argument the
per-material small transactions in ``material_ttl.py`` make).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.execution_retention import (
    ExecutionRetentionQueriesMixin,
    execution_retention_queries_from_dsn,
)
from server.app.services.instance_settings_store import InstanceSettingsStore

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0

_CURSOR_BLOCK = "execution_retention_cursor"

# The scan-start sentinel mirrors ``cleanup_sweep_store._EMPTY_CURSOR``:
# with no stored mark the sweep starts from the beginning of the expired
# tail (``datetime.min`` compares below every stored timestamp; the id
# sentinel must be below every real id, so ints start at 0 and text ids at
# the empty string).
_SCAN_START_AT = datetime.min.replace(tzinfo=UTC)


def execution_retention_days(connect_source: ConnectSource) -> int:
    """Effective execution retention in days (0 = disabled); read fresh.

    Same contract as ``material_ttl.materials_ttl_days``: the value is
    consumed at sweep time, so it is read from the DB document on every use
    and edits take effect without a restart. Defensive against out-of-band
    writes: anything but a positive int degrades to 0 (disabled).
    """
    stored = InstanceSettingsStore(connect_source).get()
    if stored is None:
        return 0
    value = stored.get("execution_retention_days", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _as_utc(value: Any) -> datetime:
    """Normalize a DB timestamp to an aware UTC datetime (keyset bound)."""
    finished = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return finished


def _queries(connect_source: ConnectSource) -> ExecutionRetentionQueriesMixin:
    """The retention queries: the JobQueries facade itself, or the bare-DSN
    adapter (``ConnectSource`` contract, #187; SQL centralization #281)."""
    if isinstance(connect_source, str):
        return execution_retention_queries_from_dsn(connect_source)
    return connect_source


class _RetentionCursor:
    """One persisted keyset cursor (``at`` column value, row id).

    The cursor rides the ``instance`` settings document (read-modify-write
    through the same KV store) so no new global_settings key is needed. The
    nested ``execution_retention_cursor`` block is not part of the admin
    contract: effective reads merge over code defaults and PUT replaces the
    whole document, so a stale cursor block disappears the next time an
    admin saves settings (same lifecycle as any extra field on the stored
    document).
    """

    def __init__(self, connect_source: ConnectSource, key: str, id_sentinel: Any) -> None:
        self._connect_source = connect_source
        self._key = key
        # The lowest possible id of this cursor's table (0 for bigint ids,
        # "" for text ids) so the first page's keyset bound selects all.
        self._id_sentinel = id_sentinel

    def load(self) -> tuple[datetime, Any]:
        """The persisted ``(at, id)`` mark, or the scan start."""
        stored = InstanceSettingsStore(self._connect_source).get() or {}
        entry = (stored.get(_CURSOR_BLOCK) or {}).get(self._key) or {}
        raw = entry.get("at")
        if not raw:
            return _SCAN_START_AT, self._id_sentinel
        return _as_utc(datetime.fromisoformat(str(raw))), entry.get("id", self._id_sentinel)

    def save(self, at: datetime, row_id: Any) -> None:
        """Advance one cursor key's mark; read-modify-write keeps the rest."""

        def _advance(document: dict[str, Any]) -> dict[str, Any]:
            document.setdefault(_CURSOR_BLOCK, {})[self._key] = {
                "at": at.isoformat(),
                "id": row_id,
            }
            return document

        InstanceSettingsStore(self._connect_source).update(_advance)


def _sweep_pages(
    queries: ExecutionRetentionQueriesMixin,
    cursor: _RetentionCursor,
    page,
    delete: Callable[[list[Any]], int],
    cutoff: datetime,
    *,
    at_column: str,
    state: str | None = None,
) -> int:
    """Page one table's expired tail, removing rows batch by batch.

    Each iteration fetches one batch (read connection) and deletes it in its
    own short transaction, then advances the persisted cursor — so an
    interrupt loses at most the in-flight batch (committed batches stay
    deleted, the cursor never rewinds) and a rerun resumes mid-tail without
    repeating committed work.
    """
    last_at, last_id = cursor.load()
    deleted = 0
    while True:
        if state is not None:
            rows = page(state, cutoff, last_at, last_id, BATCH_SIZE)
        else:
            rows = page(cutoff, last_at, last_id, BATCH_SIZE)
        if not rows:
            return deleted
        ids = (
            [row["id"] for row in rows]
            if state is None
            else [str(row["execution_id"]) for row in rows]
        )
        # Count actual deletions, not page size: a concurrent job deletion
        # can cascade some of these rows away between the page read and this
        # short delete transaction (independent-review P2 on #354).
        deleted += delete(ids)
        last_at = _as_utc(rows[-1][at_column])
        last_id = rows[-1]["execution_id"] if state is not None else rows[-1]["id"]
        cursor.save(last_at, last_id)
        if len(rows) < BATCH_SIZE:
            return deleted


def sweep_expired_executions(
    connect_source: ConnectSource, *, now: datetime | None = None
) -> dict[str, int]:
    """Remove terminal execution-plane rows past the retention window.

    Returns per-table deleted counts (all zero when retention is disabled).
    """
    days = execution_retention_days(connect_source)
    if days <= 0:
        return {"agent_execution_requests": 0, "executor_leases": 0, "node_run_token_usage": 0}
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    queries = _queries(connect_source)
    requests = sum(
        _sweep_pages(
            queries,
            _RetentionCursor(connect_source, f"requests:{state}", ""),
            queries.page_terminal_agent_requests,
            queries.delete_agent_requests,
            cutoff,
            at_column="finished_at",
            state=state,
        )
        for state in ("done", "cancelled")
    )
    leases = _sweep_pages(
        queries,
        _RetentionCursor(connect_source, "leases", ""),
        queries.page_inactive_leases,
        queries.delete_leases,
        cutoff,
        at_column="expires_at",
    )
    usage = _sweep_pages(
        queries,
        _RetentionCursor(connect_source, "token_usage", 0),
        queries.page_finished_token_usage,
        queries.delete_token_usage,
        cutoff,
        at_column="created_at",
    )
    if requests or leases or usage:
        logger.info(
            "execution retention sweep deleted %d request(s), %d lease(s), %d token usage row(s)",
            requests,
            leases,
            usage,
        )
    return {
        "agent_execution_requests": requests,
        "executor_leases": leases,
        "node_run_token_usage": usage,
    }
