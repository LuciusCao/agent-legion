"""run_batches queries (#1117): the async runs mode's batch state row.

The runs-async feeder state machine lives on this table (schema v99;
docs/architecture/runs-async-mode.md). All transitions are guarded atomic
UPDATEs — the check-and-set races (pause landing mid-chunk, resubmission
meeting an in-flight feed, crash reclaim) are decided by the WHERE clause,
never by a read-then-write sequence in the caller (多步变更纪律: revalidate
the DECIDING state inside the mutation).
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class RunBatchQueriesMixin(ConnectionQueriesMixin):
    def create_run_batch(
        self,
        run_id: str,
        workspace_id: str,
        target_spec: dict[str, Any],
        items_total: int,
        created_by: str,
    ) -> None:
        """Insert the batch row for a freshly accepted async run.

        Blind-retry safe: identical resubmission resolves to the same run id
        and the ON CONFLICT arm is a no-op — the caller then applies
        ``reset_run_batch_for_resubmit`` for the terminal-state self-heal.
        """
        spec_json = json.dumps(target_spec, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                insert into run_batches(
                  run_id, workspace_id, status, target_spec_json, items_total, created_by
                ) values (%s, %s, 'pending', %s, %s, %s)
                on conflict(run_id) do nothing
                """,
                (run_id, workspace_id, spec_json, items_total, created_by),
            )

    def reset_run_batch_for_resubmit(
        self, run_id: str, target_spec: dict[str, Any], items_total: int
    ) -> dict[str, Any] | None:
        """Self-heal a terminal batch on identical resubmission (one UPDATE).

        failed/cancelled reset unconditionally; completed resets only when the
        run's live job count fell below the recorded total (jobs deleted after
        completion — the ``requeue_completed_run_if_depleted`` precedent). The
        re-walk is idempotent: the dedup probe filters already-created jobs.
        ``archived_at`` is cleared too (#1157): the healed batch is live again
        and must leave the task center's archived view. Returns the reset row,
        or None when the batch is intact (the caller then leaves the run row
        untouched as well).
        """
        spec_json = json.dumps(target_spec, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                """
                update run_batches
                set status='pending', next_index=0, jobs_created=0, chunk_errors_json='[]',
                    error_message='', finished_at=null, archived_at=null,
                    updated_at=current_timestamp,
                    target_spec_json=%s, items_total=%s
                where run_id=%s and (
                  status in ('failed', 'cancelled')
                  or (status='completed'
                      and (select count(*) from jobs where run_id=%s) < jobs_created)
                )
                returning *
                """,
                (spec_json, items_total, run_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def get_run_batch(self, run_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from run_batches where run_id=%s", (run_id,)).fetchone()
        return dict(row) if row else None

    def claim_run_batch(self, stale_after_seconds: int = 600) -> dict[str, Any] | None:
        """Claim one pending (or stale-running) batch; SKIP LOCKED, intake precedent."""
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from run_batches
                where status='pending'
                   or (status='running' and updated_at < current_timestamp - (%s * interval '1 second'))
                order by updated_at, created_at
                for update skip locked
                limit 1
                """,
                (stale_after_seconds,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update run_batches set status='running', updated_at=current_timestamp"
                " where run_id=%s",
                (row["run_id"],),
            )
            claimed = conn.execute(
                "select * from run_batches where run_id=%s", (row["run_id"],)
            ).fetchone()
        return dict(claimed) if claimed else None

    def advance_run_batch(
        self,
        run_id: str,
        *,
        next_index: int,
        jobs_created: int,
        chunk_errors: list[dict[str, Any]],
        error_message: str,
        done: bool,
    ) -> dict[str, Any] | None:
        """CAS cursor advance (pause-wins): misses when the row left 'running'.

        Non-final advances return the row to 'pending' — 'running' is the
        in-tick claim state only; leaving it set would park the batch until
        the stale-claim timeout (the intake consumer's queued-between-chunks
        precedent). None = a pause/cancel landed mid-chunk; the feeder must
        NOT overwrite the operator intent (the committed chunk is replay-safe
        via dedup).
        """
        errors_json = json.dumps(chunk_errors, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                """
                update run_batches
                set next_index=%s, jobs_created=%s, chunk_errors_json=%s, error_message=%s,
                    status=case when %s then 'completed' else 'pending' end,
                    finished_at=case when %s then current_timestamp else null end,
                    updated_at=current_timestamp
                where run_id=%s and status='running'
                returning *
                """,
                (next_index, jobs_created, errors_json, error_message, done, done, run_id),
            ).fetchone()
        return dict(row) if row else None

    def transition_run_batch(
        self, run_id: str, *, from_statuses: tuple[str, ...], to_status: str
    ) -> dict[str, Any] | None:
        """Operator control CAS (pause/resume/cancel) and the watermark release.

        None = the row is not in one of ``from_statuses`` (the caller maps the
        miss to a 409 on the API surface; the feeder's watermark release treats
        it as already-handled). Terminal transitions stamp finished_at.
        """
        terminal = to_status in ("completed", "cancelled", "failed")
        with self.connect() as conn:
            row = conn.execute(
                """
                update run_batches
                set status=%s, updated_at=current_timestamp,
                    finished_at=case when %s then current_timestamp else finished_at end
                where run_id=%s and status = any(%s)
                returning *
                """,
                (to_status, terminal, run_id, list(from_statuses)),
            ).fetchone()
        return dict(row) if row else None

    def fail_run_batch(self, run_id: str, error_message: str) -> None:
        """Batch-level containment: deterministic failure (corrupt spec, vanished
        revision) marks the row failed from any non-terminal state."""
        with self.connect() as conn:
            conn.execute(
                """
                update run_batches
                set status='failed', error_message=%s, updated_at=current_timestamp,
                    finished_at=current_timestamp
                where run_id=%s and status not in ('completed', 'cancelled', 'failed')
                """,
                (error_message[:1000], run_id),
            )
