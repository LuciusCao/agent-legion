"""Guarded bookkeeping for operator-interrupted batch operation chunks (#1397)."""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.job_batch_operation_resume import JobBatchOperationResumeQueriesMixin


class JobBatchOperationControlQueriesMixin(JobBatchOperationResumeQueriesMixin):
    def record_interrupted_job_batch_operation(
        self,
        op_id: str,
        *,
        next_index: int,
        counts: dict[str, int],
        chunk_errors: list[dict[str, Any]],
        error_message: str,
        output: dict[str, Any] | None = None,
        target_spec: dict[str, Any] | None = None,
        items_total: int | None = None,
    ) -> dict[str, Any] | None:
        """Commit an interrupted chunk without changing the operator's status.

        A quick resume may make this update miss after the running CAS missed;
        the old cursor then remains in place and done-seq replay skips the
        already-recorded work. Neither operator status nor finished_at moves.
        """
        errors_json = json.dumps(chunk_errors, ensure_ascii=False, sort_keys=True)
        output_json = json.dumps(output or {}, ensure_ascii=False, sort_keys=True)
        spec_json = (
            json.dumps(target_spec, ensure_ascii=False, sort_keys=True)
            if target_spec is not None
            else None
        )
        with self.connect() as conn:
            row = conn.execute(
                """
                update job_batch_operations
                set next_index=%s, succeeded_count=%s, skipped_count=%s, failed_count=%s,
                    chunk_errors_json=%s, error_message=%s,
                    output_json=case when %s then %s else output_json end,
                    target_spec_json=coalesce(%s, target_spec_json),
                    items_total=coalesce(%s, items_total), updated_at=current_timestamp
                where id=%s and status in ('paused', 'cancelled')
                returning *
                """,
                (
                    next_index,
                    counts.get("succeeded", 0),
                    counts.get("skipped", 0),
                    counts.get("failed", 0),
                    errors_json,
                    error_message,
                    output is not None,
                    output_json,
                    spec_json,
                    items_total,
                    op_id,
                ),
            ).fetchone()
        return dict(row) if row else None
