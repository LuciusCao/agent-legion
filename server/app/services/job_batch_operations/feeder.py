"""JobBatchOpsFeeder (#946): walks accepted batch operations job by job.

The RunBatchFeeder (#1117) mapped from "job creation" onto "mutating
existing jobs": one claimed operation per tick, one bounded chunk per tick, chunk-level
error containment (converted to per-job ``failed`` results so no selected job
is left without a verdict), batch-level failure containment, stale claim
reclaim, pause-wins CAS advance. Per-job mutations execute through the same
service entry points as the sync path (handlers.run_chunk), each in its own
transaction exactly as before — the EXEC-GENERATION-002 write surfaces are
therefore unchanged, and the two new bookkeeping tables stay outside the
pinned families (run_batches precedent).

Crash/replay idempotency: results are recorded per chunk BEFORE the cursor
advance, keyed (op_id, seq); a reclaimed chunk first probes the recorded seqs
and never re-executes them (rerun/package are not naturally idempotent —
this guard is what makes the replay safe). The remaining window is a crash
between one job's committed mutation and its result row write
(single-statement
gap, the intake consumer's committed-jobs-stay acceptance); the package
completion phase re-runs only when the crash lands between the packaging
side effects and the final advance.

Watermark gate: kinds whose per-job effect enqueues execution work (rerun /
rerun_by_failure / run_to / resume / upgrade_workflow / approve — approval
unblocks downstream nodes) drip against the same workspace backlog watermark
as the runs feeder; the other kinds don't grow the backlog and run ungated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_batch_operations.handlers import (
    BatchOpsExecutors,
    run_chunk,
    run_completion,
)
from server.app.services.job_batch_operations.selection import load_chunk
from server.app.services.run_batches.feeding_gate import (
    WATERMARK_JOB_BATCH_OPERATION_KINDS as _WATERMARK_KINDS,
)
from server.app.services.run_batches.feeding_gate import run_batch_watermark, workspace_backlog
from server.app.settings import Settings

logger = logging.getLogger(__name__)

# Per-tick job chunk. Each job mutation is its own transaction (lock-and-
# release), so the chunk only bounds the claim/advance round trips and the
# crash-replay re-scan window.
JOB_BATCH_OPS_CHUNK_SIZE = 200
MAX_CHUNK_ERRORS = 20
# run_to/pause/resume loop the raw id list (duplicates included), so their
# chunk results map back to seqs positionally; every other kind's frozen ids
# are unique and map by job_id.
_POSITIONAL_KINDS = frozenset({"run_to", "pause", "resume"})


class JobBatchOpsFeeder:
    def __init__(
        self, job_db: JobQueries, settings: Settings, executors: BatchOpsExecutors
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        self.executors = executors

    def _watermark(self) -> int:
        return run_batch_watermark(self.settings)

    def _workspace_backlog(self, workspace_id: str) -> int:
        return workspace_backlog(self.job_db, workspace_id)

    def consume_once(self) -> bool:
        operation = self.job_db.claim_job_batch_operation()
        if operation is None:
            return False
        try:
            return self._consume_chunk(operation)
        except Exception as exc:
            # #204 broad-except audit: batch-level containment, the
            # RunBatchFeeder.consume_once contract verbatim. _consume_chunk's
            # inner catch already converts per-chunk execution failures into
            # per-job failed results; what reaches here is the bookkeeping
            # itself failing (corrupt target_spec_json, a DB failure mid
            # status write, the package completion phase raising). The
            # claimed operation must be recorded failed (or it would be
            # re-claimed forever); if the fail-write itself raises, that
            # propagates to the background loop's backoff arm and the row
            # stays claimable for a retry — both correct.
            logger.exception("job batch operation %s failed", operation["id"])
            self.job_db.fail_job_batch_operation(str(operation["id"]), str(exc)[:1000])
        return True

    def _consume_chunk(self, operation: dict[str, Any]) -> bool:
        """One chunk of the claimed operation; False = nothing fed (watermark)."""
        op_id = str(operation["id"])
        workspace_id = str(operation["workspace_id"])
        kind = str(operation["kind"])
        payload = json.loads(str(operation["target_spec_json"]))
        params: dict[str, Any] = payload.get("params") or {}
        start = int(operation["next_index"])

        # Watermark gate (work-creating kinds only): release the claim back to
        # pending and report no-work so the loop idles instead of hot-spinning;
        # claim ordering by updated_at round-robins across workspaces.
        if kind in _WATERMARK_KINDS and self._workspace_backlog(workspace_id) >= self._watermark():
            self.job_db.transition_job_batch_operation(
                op_id, from_statuses=("running",), to_status="pending"
            )
            return False

        ids, buffer_start, items_total, payload, spec_changed = load_chunk(
            self.job_db, workspace_id, payload, start, JOB_BATCH_OPS_CHUNK_SIZE
        )
        end = min(start + JOB_BATCH_OPS_CHUNK_SIZE, items_total)
        done_seqs = self.job_db.list_job_batch_operation_done_seqs(op_id, start, end)
        todo = [(seq, ids[seq - buffer_start]) for seq in range(start, end) if seq not in done_seqs]
        chunk_errors = json.loads(str(operation["chunk_errors_json"] or "[]"))
        if todo:
            try:
                results = run_chunk(
                    self.executors, workspace_id, kind, [job_id for _, job_id in todo], params
                )
                rows = self._map_results(kind, todo, results)
            except Exception as exc:
                # Chunk-level containment: convert the failure into per-job
                # failed results (the #759 P1 upgrade precedent) so every
                # selected job ends with a verdict, plus one chunk_errors
                # entry; the remaining chunks proceed.
                # #204 broad-except audit: the chunk's outcome space is the
                # union of eleven kinds' service surfaces (contract errors are
                # already per-job results; what propagates is psycopg/pool
                # failures and defects) — not enumerable as a business family,
                # and the results table is the recovery record.
                logger.exception("job batch operation %s chunk [%s:%s] failed", op_id, start, end)
                if len(chunk_errors) < MAX_CHUNK_ERRORS:
                    chunk_errors.append({"chunk_start": start, "error": str(exc)[:300]})
                rows = [
                    self._result_row(seq, job_id, self._internal_error_result(kind, job_id, exc))
                    for seq, job_id in todo
                ]
            self.job_db.insert_job_batch_operation_results(op_id, rows)

        stream = payload.get("stream")
        done = end >= items_total and (stream is None or stream["next_cursor"] is None)
        output: dict[str, Any] | None = None
        if done and kind == "package":
            # Replay guard: a stale reclaim re-walks the final chunk with every
            # seq recorded (todo empty); the packaging side effects must not
            # run twice, so reuse the already-stored output when present.
            fresh = self.job_db.get_job_batch_operation(op_id)
            existing = json.loads(str(fresh["output_json"] or "{}")) if fresh else {}
            if existing:
                output = existing
            elif fresh is not None and fresh["status"] == "running":
                output = run_completion(self.executors, self.job_db, workspace_id, kind, op_id)

        counts = self.job_db.count_job_batch_operation_results(op_id)
        error_message = "; ".join(
            f"chunk {error['chunk_start']}: {error['error']}" for error in chunk_errors[-5:]
        )[:1000]
        advanced = self.job_db.advance_job_batch_operation(
            op_id,
            next_index=end,
            counts=counts,
            chunk_errors=chunk_errors,
            error_message=error_message,
            done=done,
            output=output,
            target_spec=payload if spec_changed else None,
            items_total=items_total,
        )
        if advanced is None:
            current = self.job_db.get_job_batch_operation(op_id)
            if current is not None and current["status"] in ("paused", "cancelled"):
                self.job_db.record_interrupted_job_batch_operation(
                    op_id,
                    next_index=end,
                    counts=counts,
                    chunk_errors=chunk_errors,
                    error_message=error_message,
                    output=output,
                    target_spec=payload if spec_changed else None,
                    items_total=items_total,
                )
        return True

    @staticmethod
    def _internal_error_result(kind: str, job_id: str, exc: Exception) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "operation": kind,
            "status": "failed",
            "node_key": None,
            "reason_code": "internal_error",
            "message": str(exc)[:300],
        }

    @staticmethod
    def _result_row(seq: int, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "seq": seq,
            "job_id": str(result.get("job_id") or job_id),
            "status": str(result.get("status") or "failed"),
            "reason_code": result.get("reason_code"),
            "message": result.get("message"),
            "payload": result,
        }

    def _map_results(
        self, kind: str, todo: list[tuple[int, str]], results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Align handler results to frozen-list seqs (positional or by job_id)."""
        if kind in _POSITIONAL_KINDS:
            if len(results) != len(todo):
                raise ValueError(
                    f"{kind} handler returned {len(results)} results for {len(todo)} jobs"
                )
            return [
                self._result_row(seq, job_id, result)
                for (seq, job_id), result in zip(todo, results, strict=True)
            ]
        by_job = {str(result.get("job_id")): result for result in results}
        rows: list[dict[str, Any]] = []
        for seq, job_id in todo:
            result = by_job.get(job_id)
            if result is None:
                # Defensive: a handler silently dropping a job must not strand
                # the cursor — record a failed verdict and keep moving.
                logger.error(
                    "job batch operation kind %s returned no result for job %s", kind, job_id
                )
                result = self._internal_error_result(kind, job_id, ValueError("no result"))
            rows.append(self._result_row(seq, job_id, result))
        return rows
