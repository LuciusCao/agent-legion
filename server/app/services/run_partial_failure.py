"""Partial-failure error for chunked run creation (#467 A3).

create_jobs_bulk commits in bounded chunks; a mid-run failure leaves earlier
chunks committed. That state is recoverable (a resubmission of the same items
resumes through the dedup filter — the same contract as the async intake
queue's chunk-error recovery), but it MUST be legible to the operator:
without the progress fields, a failed response would hide that thousands of
jobs were actually created.

Kept out of ``run_service.py`` for the file-size budget, mirroring the
``run_bundle_candidate`` precedent.
"""

from __future__ import annotations

from typing import Any, Never

from server.app.services.run_partial_failure_compensation import compensate_partial_creation
from server.app.services.run_partial_failure_contract import (
    PartialRunCreationError,
    partial_failure_message,
)

__all__ = [
    "PartialRunCreationError",
    "compensate_partial_creation",
    "partial_failure_message",
    "raise_compensated_partial_creation",
]


def raise_compensated_partial_creation(job_db: Any, run_id: str, exc: Exception) -> Never:
    """Compensate, then raise the structured or original exception."""
    partial_error = compensate_partial_creation(job_db, run_id, exc)
    if partial_error is not None:
        raise partial_error from exc
    raise exc
