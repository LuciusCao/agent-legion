"""Compensation protocol for a failure during chunked run creation."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from server.app.services.run_partial_failure_contract import (
    PartialRunCreationError,
    partial_failure_message,
)

logger = logging.getLogger(__name__)


def compensate_partial_creation(
    job_db: Any, run_id: str, exc: Exception
) -> PartialRunCreationError | None:
    """Remove an empty run or mark a partially committed run failed."""
    try:
        committed = job_db.count_jobs_in_run(run_id)
    except Exception:
        # #204 broad-except audit: progress bookkeeping must not mask the
        # original creation error; degrade to the empty-run branch and log.
        logger.exception("run %s progress count failed", run_id)
        committed = 0
    if committed > 0:
        try:
            job_db.update_intake_run(
                run_id,
                created_count=committed,
                status="failed",
                error_message=partial_failure_message(committed, exc),
            )
        except (OSError, psycopg.Error) as exc2:
            # #204: compensation-only catch — a DB failure here must not mask
            # the original creation error.
            logger.warning("run %s partial-failure marking failed: %s", run_id, exc2)
        return PartialRunCreationError(
            partial_failure_message(committed, exc),
            run_id=run_id,
            created_so_far=committed,
        )
    try:
        job_db.delete_run_without_jobs(run_id)
    except (OSError, psycopg.Error) as exc3:
        # #204: same compensation-only catch — programming errors propagate.
        logger.warning("run %s left orphaned after job creation failed: %s", run_id, exc3)
    return None
