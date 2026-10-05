from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.batch_queue_sql import RUN_REQUEUE_DEPLETED
from server.app.jobs.queries.connection import ConnectionQueriesMixin


class RunQueueQueriesMixin(ConnectionQueriesMixin):
    def count_jobs_in_run(self, run_id: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as count from jobs where run_id=%s", (run_id,)
            ).fetchone()
        return int(row["count"]) if row else 0

    def claim_intake_run(self, stale_after_seconds: int = 600) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from runs
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
                update runs
                set status='processing', error_message='', updated_at=current_timestamp
                where id=%s
                """,
                (row["id"],),
            )
            claimed = conn.execute("select * from runs where id=%s", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

    # Requeue a completed run whose jobs were (partially) deleted; None when
    # the run is intact or no longer completed (the UPDATE's guard clause
    # makes the check-and-transition atomic against consumer claims).
    def requeue_completed_run_if_depleted(
        self, run_id: str, queue_payload: dict[str, Any], recorded_count: int
    ) -> dict[str, Any] | None:
        payload_json = json.dumps(queue_payload, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                RUN_REQUEUE_DEPLETED, (payload_json, run_id, run_id, run_id, recorded_count)
            ).fetchone()
        return dict(row) if row else None

    def update_intake_run(
        self,
        run_id: str,
        *,
        queue_payload: dict[str, Any] | None = None,
        created_count: int | None = None,
        status: str,
        error_message: str = "",
    ) -> dict[str, Any]:
        payload_json = (
            json.dumps(queue_payload, ensure_ascii=False, sort_keys=True)
            if queue_payload is not None
            else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                update runs
                set queue_payload_json=coalesce(%s, queue_payload_json),
                    created_count=coalesce(%s, created_count), status=%s, error_message=%s,
                    updated_at=current_timestamp
                where id=%s
                """,
                (payload_json, created_count, status, error_message, run_id),
            )
            row = conn.execute("select * from runs where id=%s", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError("run update did not return a row")
        return dict(row)
