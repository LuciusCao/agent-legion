from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.batch_queue_sql import BATCH_REQUEUE_DEPLETED


class BatchQueueQueriesMixin(JobQueriesBase):
    def count_jobs_in_batch(self, batch_id: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as count from jobs where batch_id=%s", (batch_id,)
            ).fetchone()
        return int(row["count"]) if row else 0

    def claim_intake_batch(self, stale_after_seconds: int = 600) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from job_batches
                where status='queued'
                   or (status='processing' and updated_at < current_timestamp - (%s * interval '1 second'))
                order by updated_at, created_at
                for update skip locked
                limit 1
                """,
                (stale_after_seconds,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                update job_batches
                set status='processing', error_message='', updated_at=current_timestamp
                where id=%s
                """,
                (row["id"],),
            )
            claimed = conn.execute("select * from job_batches where id=%s", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

    # Requeue a completed batch whose jobs were (partially) deleted; None when
    # the batch is intact or no longer completed (the UPDATE's guard clause
    # makes the check-and-transition atomic against consumer claims).
    def requeue_completed_batch_if_depleted(
        self, batch_id: str, source_payload: dict[str, Any], recorded_count: int
    ) -> dict[str, Any] | None:
        payload_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                BATCH_REQUEUE_DEPLETED, (payload_json, batch_id, batch_id, batch_id, recorded_count)
            ).fetchone()
        return dict(row) if row else None

    def update_intake_batch(
        self,
        batch_id: str,
        *,
        source_payload: dict[str, Any] | None = None,
        created_count: int | None = None,
        status: str,
        error_message: str = "",
    ) -> dict[str, Any]:
        payload_json = (
            json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
            if source_payload is not None
            else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                update job_batches
                set source_payload_json=coalesce(%s, source_payload_json),
                    created_count=coalesce(%s, created_count), status=%s, error_message=%s,
                    updated_at=current_timestamp
                where id=%s
                """,
                (payload_json, created_count, status, error_message, batch_id),
            )
            row = conn.execute("select * from job_batches where id=%s", (batch_id,)).fetchone()
        if row is None:
            raise RuntimeError("job batch update did not return a row")
        return dict(row)
