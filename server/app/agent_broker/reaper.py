"""Bundle-dir GC for terminal Agent executions (watermark persisted, #357).
The sweeper calls every few seconds, so no pass can re-read every terminal
manifest in history. Without a watermark the first pass of a process scans
all terminal rows (streamed in chunks, so scan memory stays independent of
table size), deferred to the background sweeper loop (#139) so a full scan
never blocks startup readiness. The watermark persists in ``global_settings``
(``ReapWatermarkStore``, #357): after a restart the first pass reloads it and
scans only the overlap window instead of all terminal rows; a missing/corrupt
document falls back to the full scan (unchanged #139 behavior). Later passes
read only rows finished within a trailing overlap window. Reaping is
idempotent."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection
from server.app.services.reap_watermark_store import ReapWatermarkStore

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

_SAFE_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
# Trailing overlap between incremental scans: a long transaction commits with
# a finished_at of its transaction start, so the window must exceed that skew.
_REAP_OVERLAP = timedelta(seconds=60)
# Scan fetch batch: a plain psycopg cursor buffers the whole result
# client-side (#128), so stream only the small authoritative execution id.
_SCAN_CHUNK_SIZE = 1000
_BUNDLE_NAME_PROJECTION = "execution_id || '.tar.gz' as bundle_name"


def reap_terminal_bundles(
    broker: AgentExecutionBroker, *, archive_max_age_seconds: float = 3600
) -> int:
    """Reclaim bundle-dir files no live execution can still need (failure
    paths leave bundles and orphaned result archives behind)."""
    if broker.bundle_dir is None:
        return 0
    watermark_store = ReapWatermarkStore(broker.database_dsn)
    if broker._reap_watermark is None:
        # First pass after startup: restore the persisted watermark (#357);
        # absent/corrupt → None → full scan below.
        broker._reap_watermark = watermark_store.load()
    reaped = 0
    if broker._reap_watermark is None:
        query = (
            f"select {_BUNDLE_NAME_PROJECTION} from agent_execution_requests"
            " where state in ('done', 'cancelled')"
        )
        params: tuple[object, ...] = ()
    else:
        # One branch per terminal state so each hits its partial finished_at
        # index (idx_agent_requests_done_recent / _cancelled_recent): a single
        # `state in (...) and (finished_at is null or ...)` predicate defeats
        # both indexes and seq-scans the whole table every sweeper pass.
        query = (
            f"select {_BUNDLE_NAME_PROJECTION} from agent_execution_requests"
            " where state='done' and finished_at >= %s"
            " union all"
            f" select {_BUNDLE_NAME_PROJECTION} from agent_execution_requests"
            " where state='cancelled' and finished_at >= %s"
        )
        params = (broker._reap_watermark, broker._reap_watermark)
    # Anchor the next watermark before the scan starts: rows that turn
    # terminal mid-scan are invisible to its snapshot, so the overlap window
    # must reach back past scan start no matter how long streaming takes.
    # Set only after the scan completes: an interrupted scan must replay in
    # full rather than skip rows it never processed.
    next_watermark = datetime.now(UTC) - _REAP_OVERLAP
    with read_connection(broker.database_dsn) as conn:
        for row in conn.stream("reap_terminal_scan", query, params, chunk_size=_SCAN_CHUNK_SIZE):
            bundle_name = str(row["bundle_name"] or "")
            if _SAFE_BUNDLE_NAME.fullmatch(bundle_name):
                target = broker.bundle_dir / bundle_name
                if target.is_file():
                    target.unlink(missing_ok=True)
                    reaped += 1
    broker._reap_watermark = next_watermark
    # Persisted only after the in-memory advance: an interrupted pass must
    # replay in full; a failed save just costs a fuller next-restart rescan.
    watermark_store.save(next_watermark)
    cutoff = time.time() - archive_max_age_seconds
    # .result-*.tmp: staging files leaked by a process crash mid-spool
    # (exception paths reclaim them, SIGKILL cannot). Age-gated like the
    # archives so a slow in-flight upload is never reaped underfoot.
    for pattern in ("*.result.tar.gz", ".result-*.tmp"):
        for orphan in broker.bundle_dir.glob(pattern):
            try:
                if orphan.stat().st_mtime < cutoff:
                    orphan.unlink(missing_ok=True)
                    reaped += 1
            except OSError:
                continue
    return reaped
