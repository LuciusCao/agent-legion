"""Worker-level audit lines for lease reclaim bursts and rejected results (#681).

Two WARNING-level, single-line JSON records (same shape discipline as the
#490 ``worker_events`` stream, but always visible at the default level —
both are operator-actionable turns, never rhythm):

- ``worker.lease_reclaim_burst``: one sweep expired ≥ ``RECLAIM_BURST_THRESHOLD``
  leases of the same Worker. Per-execution ``execution.lease_expired`` lines
  scatter a mass reclaim across hundreds of rows; this names it once per
  Worker per sweep, with the count, how many will rerun vs hit the requeue
  limit, how many were deferred (#566) and the Worker's last authenticated
  contact.
- ``execution.result_rejected``: a terminal result report was refused (409)
  because the attempt no longer owns its lease. The Worker drops a refused
  result, so this line is the Host's only record that finished work — and
  whether it carried output artifacts — was discarded, and why (``reason``
  is read off the request row at rejection time). No dead-letter storage:
  the line is the audit trail.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from server.app.agent_broker import worker_events
from server.app.db.transaction import read_connection
from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_FLAG

logger = logging.getLogger(__name__)

# Expired leases of ONE Worker in ONE sweep that make it a burst. A lone
# attempt thread dying is per-execution noise; ten at once is the Worker (or
# its link to the Host) — well under any real concurrency, well above chance.
RECLAIM_BURST_THRESHOLD = 10
# Execution-id sample carried by a burst line (the full set is in the
# per-execution lease_expired lines; the sample is for grep pivoting).
_SAMPLE_SIZE = 5


def _emit(event: str, payload: dict[str, Any]) -> None:
    body = {"event": event, "ts": datetime.now(UTC).isoformat(), **payload}
    logger.warning(json.dumps(body, ensure_ascii=False, default=str, sort_keys=True))


class ReclaimTally:
    """Per-sweep, per-Worker count of expired (deleted) leases."""

    def __init__(self, requeue_limit: int) -> None:
        self._requeue_limit = requeue_limit
        self._expired: dict[str, list[str]] = {}
        self._limit_exceeded: Counter[str] = Counter()

    def note_expired(self, row: Any, generation_stale: bool) -> None:
        """Record one lease the sweep is deleting (+ the #490 per-row event).

        Outcome accounting follows the sweep's actual branch order (#896
        review P2): a stale-generation row is CANCELLED before the requeue
        limit is consulted, so only a current-generation row with attempt >
        limit takes the force-fail branch; requeued ids come from the sweep's
        own result in ``report``; every other expired row was cancelled
        (stale generation, or a node that already went terminal)."""
        worker_events.note_lease_expired(row, self._requeue_limit)
        worker_id = str(row["worker_id"])
        self._expired.setdefault(worker_id, []).append(str(row["execution_id"]))
        if not generation_stale and int(row["attempt"]) > self._requeue_limit:
            self._limit_exceeded[worker_id] += 1

    def report(self, deferral: Any, requeued: list[str]) -> None:
        """Emit one burst line per Worker at/over the threshold. Called after
        the sweep transaction commits, so a rolled-back sweep reports nothing."""
        requeued_ids = set(requeued)
        for worker_id, executions in sorted(self._expired.items()):
            if len(executions) < RECLAIM_BURST_THRESHOLD:
                continue
            last_seen = deferral.last_seen(worker_id)
            rerun = sum(1 for execution_id in executions if execution_id in requeued_ids)
            failed = self._limit_exceeded[worker_id]
            _emit(
                "worker.lease_reclaim_burst",
                {
                    "worker_id": worker_id,
                    "reclaimed": len(executions),
                    "requeued": rerun,
                    "requeue_limit_exceeded": failed,
                    "cancelled": len(executions) - rerun - failed,
                    "deferred": deferral.deferred_count(worker_id),
                    "worker_last_seen_at": last_seen.isoformat() if last_seen else None,
                    "sample_execution_ids": executions[:_SAMPLE_SIZE],
                },
            )


def _ownership_reason(conn: Any, execution_id: str, worker_id: str, lease_id: str) -> str:
    row = conn.execute(
        "select state, worker_id, lease_id from agent_execution_requests where execution_id=%s",
        (execution_id,),
    ).fetchone()
    if row is None:
        return "missing"
    if row["state"] == "queued":
        return "requeued"  # the sweep reclaimed the lease; awaiting re-claim
    if row["state"] in ("claimed", "reporting"):
        if str(row["worker_id"]) != worker_id:
            return "reassigned"  # another Worker owns the new attempt
        if str(row["lease_id"]) != lease_id:
            return "superseded"  # this Worker re-claimed it under a new lease
        return "lease_not_active"
    return f"request_{row['state']}"  # done / cancelled / failed


def reject_result(
    dsn: Any,
    execution_id: str,
    worker_id: str,
    lease_id: str,
    record: dict[str, Any],
    *,
    stage: str,
    detail: str,
    archive_bytes: int | str | None = None,
    payload: Any = None,
) -> HTTPException:
    """Audit one refused terminal report; returns the 409 for the caller to
    raise. Never raises itself (observation must not mask the 409)."""
    worker_events.note_execution_finished_rejected(execution_id, worker_id, payload)
    try:
        with read_connection(dsn) as conn:
            reason = _ownership_reason(conn, execution_id, worker_id, lease_id)
    except Exception:
        # #204 broad-except audit: the reason lookup is a best-effort read on
        # the 409 path (pool exhaustion during the very overload this audits
        # is the expected failure); the audit line still goes out with
        # reason "unknown" and the caller's 409 is unaffected.
        reason = "unknown"
    artifacts = record.get("output_artifacts") or {}
    _emit(
        "execution.result_rejected",
        {
            "execution_id": execution_id,
            "worker_id": worker_id,
            "lease_id": lease_id,
            "stage": stage,
            "reason": reason,
            "status": record.get("status"),
            "exit_code": record.get("exit_code"),
            "carries_artifacts": bool(artifacts)
            or record.get(RESULT_OUTPUT_ARTIFACTS_FLAG) is True,
            "output_artifact_count": len(artifacts),
            # Declared Content-Length (precheck) or staged size (commit).
            "archive_bytes": int(str(archive_bytes)) if str(archive_bytes).isdigit() else None,
        },
    )
    return HTTPException(status_code=409, detail=detail)


def precheck_result_owner(
    broker: Any,
    execution_id: str,
    worker_id: str,
    lease_id: str,
    record: dict[str, Any],
    declared_bytes: str | None,
) -> None:
    """The result route's cheap ownership pre-check BEFORE spooling the body
    (a stale lease would otherwise write up to max_archive_bytes for
    nothing); ``commit_agent_result`` re-checks under the commit to stay
    TOCTOU-safe. Blocking (DB reads) — the route runs it in the threadpool.
    Raises the audited 409 when the attempt no longer owns its lease."""
    payload = broker.claimed_payload(execution_id, worker_id)
    if payload is None or str(payload["lease_id"]) != lease_id:
        raise reject_result(
            broker.database_dsn,
            execution_id,
            worker_id,
            lease_id,
            record,
            stage="precheck",
            detail="execution is not owned by this Worker",
            archive_bytes=declared_bytes,
        )
