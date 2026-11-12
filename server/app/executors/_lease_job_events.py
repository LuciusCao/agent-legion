"""Post-commit job-update broadcasts for executor lease writes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.events.aggregator import record_job_update

if TYPE_CHECKING:
    from server.app.executors.leases import ExecutorLeaseRepository

logger = logging.getLogger("server.app.executors.leases")


def broadcast_job_update(repo: ExecutorLeaseRepository, job_id: str) -> None:
    try:
        if repo.job_db is None or repo.job_event_manager is None:
            return
        if repo.job_event_buffer is not None:
            record_job_update(repo.job_db, repo.job_event_buffer, job_id)
            return
        job = repo.job_db.get_job(job_id)
        workspace_id = str(job.get("workspace_id", "")) if job else ""
        if not workspace_id:
            return
        stats = repo.job_db.count_jobs_by_status(workspace_id)
        repo.job_event_manager.broadcast_job_updated(workspace_id, job_id, stats)
    except Exception:
        # #204 broad-except audit: fire-and-forget observability, called
        # only AFTER the lease/write transaction already committed (every
        # call site sits below the `with write_transaction` block). A SSE
        # refresh failure must never roll back or fail a claim/finish that
        # already succeeded — the stats are trigger-maintained
        # (DB-JOB-STATUS-COUNTS-001), so the very next state change
        # re-broadcasts the correct numbers and the missed one self-heals.
        # The outcome space here is the DB read surface plus the bus, not
        # a business family; logger.exception keeps the traceback.
        logger.exception("Failed to broadcast job update for %s", job_id)
