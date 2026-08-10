"""Bundle-dir garbage collection for terminal Agent executions.

Called by the sweeper every few seconds, so it cannot re-read every terminal
manifest in history. First call after startup scans all terminal rows; later
calls read only rows finished within a trailing overlap window
(``broker._reap_watermark``). Reaping is idempotent.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker

_SAFE_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")

# Trailing overlap between incremental scans: a long transaction commits with
# a finished_at of its transaction start, so the window must exceed that skew.
_REAP_OVERLAP = timedelta(seconds=60)


def reap_terminal_bundles(
    broker: AgentExecutionBroker, *, archive_max_age_seconds: float = 3600
) -> int:
    """Reclaim bundle-dir files that no live execution can still need (GC half
    of the result route's contract: failure paths leave bundles and orphaned
    result archives behind)."""
    if broker.bundle_dir is None:
        return 0
    reaped = 0
    if broker._reap_watermark is None:
        # First call after startup scans all terminal rows (including any
        # should-never-happen terminal row with finished_at NULL).
        query = "select manifest_json from agent_execution_requests where state in ('done', 'cancelled')"
        params: tuple[object, ...] = ()
    else:
        # One branch per terminal state so each hits its partial finished_at
        # index (idx_agent_requests_done_recent / _cancelled_recent). A single
        # `state in (...) and (finished_at is null or ...)` query defeats both
        # indexes and seq-scans the whole table every sweeper pass.
        query = (
            "select manifest_json from agent_execution_requests"
            " where state='done' and finished_at >= %s"
            " union all"
            " select manifest_json from agent_execution_requests"
            " where state='cancelled' and finished_at >= %s"
        )
        params = (broker._reap_watermark, broker._reap_watermark)
    with read_connection(broker.database_dsn) as conn:
        rows = conn.execute(query, params).fetchall()
    broker._reap_watermark = datetime.now(UTC) - _REAP_OVERLAP
    for row in rows:
        try:
            manifest = json.loads(row["manifest_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        bundle_name = str(manifest.get("bundle_name", ""))
        if _SAFE_BUNDLE_NAME.fullmatch(bundle_name):
            target = broker.bundle_dir / bundle_name
            if target.is_file():
                target.unlink(missing_ok=True)
                reaped += 1
    cutoff = time.time() - archive_max_age_seconds
    for orphan in broker.bundle_dir.glob("*.result.tar.gz"):
        try:
            if orphan.stat().st_mtime < cutoff:
                orphan.unlink(missing_ok=True)
                reaped += 1
        except OSError:
            continue
    return reaped
