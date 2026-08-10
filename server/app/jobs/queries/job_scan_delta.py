"""Watermark delta query for the workflow worker's incremental scan.

Split from ``job_scan_marks`` to keep that module within its size budget.
See ``server.app.workflow_worker.mark_scan`` for the watermark state machine
this query serves (DB-SCAN-INCREMENTAL-001).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.job_scan_marks import _ACTIVE_MARK_COLUMNS


class JobScanDeltaMixin(JobQueriesBase):
    def list_changed_job_marks(self, workflow_key: str, since: Any) -> list[dict[str, Any]]:
        """Lightweight rows touched after ``since`` (watermark delta scan).

        No status filter: rows that turned terminal must stay visible so the
        caller can drop them from its cache. Every mutation that matters to
        the scheduler bumps ``updated_at`` (DB-SCAN-INCREMENTAL-001).
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_ACTIVE_MARK_COLUMNS} from jobs"
                " where workflow_key=%s and updated_at > %s"
                " order by updated_at",
                (workflow_key, since),
            ).fetchall()
        return [dict(row) for row in rows]
