"""Incremental job-mark scan for the workflow worker (DB-SCAN-INCREMENTAL-001).

Historically every poll pass pulled the marks of *all* non-terminal jobs of
every registered workflow (tens of thousands of rows under backlog) just to
diff them against the per-job evaluation cache. This module keeps the marks
in memory and turns the per-pass refresh into a watermark delta query:
``list_changed_job_marks`` returns only rows whose ``updated_at`` moved since
the previous pass — usually zero to a few dozen rows.

Correctness rests on one invariant: every ``jobs``-row mutation that matters
to the scheduler bumps ``updated_at`` (claim, finish-driven status sync,
execution control, rerun). Two safety nets bound the damage of a missed or
late-committing row:

- the delta lower bound slides with the database clock minus a small overlap
  window, so rows committing slightly out of timestamp order are re-fetched
  (upserts are idempotent) without pinning the bound to a past burst;
- a periodic full rescan (``FULL_RESCAN_SECONDS``) replaces the whole set,
  pruning rows deleted without leaving a delta trace and catching any row a
  longer-than-overlap transaction hid from the delta.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

TERMINAL_STATUSES = ("completed", "failed")
FULL_RESCAN_SECONDS = 60.0
WATERMARK_OVERLAP_SECONDS = 5.0


def _parse_ts(value: Any) -> datetime | None:
    """Timestamps arrive as ISO strings (project connection convention)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass
class _WorkflowScanState:
    marks: dict[str, dict[str, Any]] = field(default_factory=dict)
    watermark: datetime | None = None  # max seen jobs.updated_at / wall horizon
    last_full_scan: float = 0.0


class MarkStore:
    """Per-workflow job-mark cache with watermark delta refresh."""

    def __init__(
        self,
        full_rescan_seconds: float = FULL_RESCAN_SECONDS,
        overlap_seconds: float = WATERMARK_OVERLAP_SECONDS,
    ) -> None:
        self._full_rescan_seconds = full_rescan_seconds
        self._overlap = timedelta(seconds=overlap_seconds)
        self._states: dict[str, _WorkflowScanState] = {}

    def refresh(self, job_db: JobQueries, workflow_key: str) -> list[dict[str, Any]]:
        """Return the current active-job marks for a workflow, delta-refreshing."""
        state = self._states.get(workflow_key)
        now = time.monotonic()
        if (
            state is None
            or state.watermark is None
            or now - state.last_full_scan >= self._full_rescan_seconds
        ):
            return self._full_refresh(job_db, workflow_key, now)
        # The lower bound slides with the database clock (minus the overlap
        # window), not just with seen rows: a burst of rows sharing one commit
        # timestamp must not pin the bound at the burst forever. The horizon
        # must come from the DB clock — ``updated_at`` is stamped by
        # ``current_timestamp``, so a Python wall clock would corrupt the
        # window when backend and Postgres run on different hosts. A
        # transaction still open past the overlap window can commit with an
        # older ``updated_at`` and be missed until the periodic full rescan
        # — the documented safety net.
        horizon = (_parse_ts(job_db.db_now()) or datetime.now(UTC)) - self._overlap
        changed = job_db.list_changed_job_marks(workflow_key, min(state.watermark, horizon))
        batch_max = state.watermark
        has_new = False
        for mark in changed:
            batch_max = max(batch_max, _parse_ts(mark.get("updated_at")) or batch_max)
            if mark.get("status") in TERMINAL_STATUSES:
                state.marks.pop(mark["id"], None)
                continue
            has_new = has_new or mark["id"] not in state.marks
            state.marks[mark["id"]] = mark
        if has_new:
            # Dict assignment appends new ids at the end, but claim order is
            # newest-first (list_active_job_marks orders by created_at desc);
            # re-establish it or fresh jobs would queue behind the backlog.
            state.marks = dict(
                sorted(
                    state.marks.items(),
                    key=lambda item: str(item[1].get("created_at") or ""),
                    reverse=True,
                )
            )
        state.watermark = max(batch_max, horizon)
        return list(state.marks.values())

    def _full_refresh(
        self, job_db: JobQueries, workflow_key: str, now: float
    ) -> list[dict[str, Any]]:
        marks = job_db.list_active_job_marks(workflow_key)
        state = self._states.setdefault(workflow_key, _WorkflowScanState())
        state.marks = {mark["id"]: mark for mark in marks}
        state.watermark = max(
            (ts for ts in (_parse_ts(mark.get("updated_at")) for mark in marks) if ts),
            default=None,
        )
        state.last_full_scan = now
        return list(state.marks.values())
