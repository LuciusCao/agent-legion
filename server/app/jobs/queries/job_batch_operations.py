"""job_batch_operations queries (#946): the batch-ops async task row + results.

The batch-ops feeder state machine lives on these tables (schema v101;
docs/architecture/batch-ops-async.md). Same discipline as ``run_batches``:
every transition is a guarded atomic UPDATE — the check-and-set races
(pause landing mid-chunk, crash reclaim) are decided by the WHERE clause,
never by a read-then-write sequence in the caller (多步变更纪律).
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# The kind vocabulary, mirrored by the DDL check constraint on
# job_batch_operations.kind. The canonical strings live here (next to the
# DDL that pins them) because server/app/services/** must stay free of
# SQL-keyword string literals (BOUNDARY-DATA-001's keyword scan).
KIND_DELETE = "delete"

JOB_BATCH_OPERATION_KINDS: tuple[str, ...] = (
    "rerun",
    "rerun_by_failure",
    KIND_DELETE,
    "run_to",
    "pause",
    "resume",
    "upgrade_workflow",
    "package",
    "clear_packed",
)

# Kinds whose sync path silently dedups/normalizes the selection (rerun's
# dict.fromkeys; delete/upgrade/package/clear-packed's seen-skip): the frozen
# id list is normalized the same way at accept time so the feeder's per-chunk
# calls reproduce the sync result set exactly. run_to/pause/resume loop the
# raw list (duplicates included), so their frozen list stays raw.
KINDS_DEDUP_SELECTION = frozenset(
    {"rerun", "rerun_by_failure", KIND_DELETE, "upgrade_workflow", "package", "clear_packed"}
)


class JobBatchOperationQueriesMixin(ConnectionQueriesMixin):
    def create_job_batch_operation(
        self,
        op_id: str,
        workspace_id: str,
        kind: str,
        target_spec: dict[str, Any],
        items_total: int,
        created_by: str,
    ) -> None:
        """Insert the operation row for a freshly accepted async batch op."""
        spec_json = json.dumps(target_spec, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                insert into job_batch_operations(
                  id, workspace_id, kind, status, target_spec_json, items_total, created_by
                ) values (%s, %s, %s, 'pending', %s, %s, %s)
                """,
                (op_id, workspace_id, kind, spec_json, items_total, created_by),
            )

    def get_job_batch_operation(self, op_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from job_batch_operations where id=%s", (op_id,)
            ).fetchone()
        return dict(row) if row else None

    def claim_job_batch_operation(self, stale_after_seconds: int = 600) -> dict[str, Any] | None:
        """Claim one pending (or stale-running) operation; SKIP LOCKED, intake precedent."""
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from job_batch_operations
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
                "update job_batch_operations set status='running', updated_at=current_timestamp"
                " where id=%s",
                (row["id"],),
            )
            claimed = conn.execute(
                "select * from job_batch_operations where id=%s", (row["id"],)
            ).fetchone()
        return dict(claimed) if claimed else None

    def insert_job_batch_operation_results(self, op_id: str, rows: list[dict[str, Any]]) -> None:
        """Chunked per-job result write; (op_id, seq) conflict = replay, skip.

        The replay guard: a chunk that committed mutations but died before the
        cursor advance is re-walked on the stale reclaim, and seqs already
        recorded here are never re-executed (the feeder probes them first);
        the ON CONFLICT arm is the last-resort absorption of a same-tick race.
        """
        if not rows:
            return
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    insert into job_batch_operation_results(
                      op_id, seq, job_id, status, reason_code, message, payload_json
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (op_id, seq) do nothing
                    """,
                    (
                        op_id,
                        row["seq"],
                        row["job_id"],
                        row["status"],
                        row.get("reason_code"),
                        row.get("message"),
                        json.dumps(row.get("payload") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )

    def list_job_batch_operation_done_seqs(self, op_id: str, start: int, end: int) -> set[int]:
        """Seqs already recorded for [start, end) — the feeder's replay skip set."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select seq from job_batch_operation_results"
                " where op_id=%s and seq >= %s and seq < %s",
                (op_id, start, end),
            ).fetchall()
        return {int(row["seq"]) for row in rows}

    def count_job_batch_operation_results(self, op_id: str) -> dict[str, int]:
        """Per-status counters, recomputed from the results table (self-healing)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select status, count(*) as n from job_batch_operation_results"
                " where op_id=%s group by status",
                (op_id,),
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def summarize_job_batch_operation_reasons(self, op_id: str) -> dict[str, int]:
        """reason_code breakdown over non-succeeded results (status endpoint)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select coalesce(reason_code, '') as reason_code, count(*) as n"
                " from job_batch_operation_results"
                " where op_id=%s and status <> 'succeeded' group by 1",
                (op_id,),
            ).fetchall()
        return {str(row["reason_code"]): int(row["n"]) for row in rows}

    def list_job_batch_operation_results_page(
        self, op_id: str, *, limit: int, after_seq: int | None = None
    ) -> list[dict[str, Any]]:
        """Keyset page of per-job results in submission (seq) order."""
        where = " where op_id=%s"
        params: list[Any] = [op_id]
        if after_seq is not None:
            where += " and seq > %s"
            params.append(after_seq)
        params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                "select seq, job_id, status, reason_code, message, payload_json"
                f" from job_batch_operation_results{where} order by seq limit %s",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_job_batch_operation_succeeded_job_ids(self, op_id: str) -> list[str]:
        """Eligible job ids for the package kind's completion phase."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select job_id from job_batch_operation_results"
                " where op_id=%s and status='succeeded' order by seq",
                (op_id,),
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def list_job_batch_operation_failed_job_ids(self, op_id: str) -> list[str]:
        """Distinct failed job ids in first-failure (seq) order — the
        retry-failed requeue's selection (#1358; positional kinds can record
        the same job at several seqs, a retry dedups them)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select job_id from job_batch_operation_results"
                " where op_id=%s and status='failed' group by job_id order by min(seq)",
                (op_id,),
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def advance_job_batch_operation(
        self,
        op_id: str,
        *,
        next_index: int,
        counts: dict[str, int],
        chunk_errors: list[dict[str, Any]],
        error_message: str,
        done: bool,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """CAS cursor advance (pause-wins): misses when the row left 'running'.

        Non-final advances return the row to 'pending' (the intake consumer's
        queued-between-chunks precedent). None = a pause/cancel landed
        mid-chunk; the feeder must NOT overwrite the operator intent — the
        committed chunk is replay-safe via the results-table replay guard.
        """
        errors_json = json.dumps(chunk_errors, ensure_ascii=False, sort_keys=True)
        output_json = json.dumps(output or {}, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                """
                update job_batch_operations
                set next_index=%s, succeeded_count=%s, skipped_count=%s, failed_count=%s,
                    chunk_errors_json=%s, error_message=%s,
                    output_json=case when %s then %s else output_json end,
                    status=case when %s then 'completed' else 'pending' end,
                    finished_at=case when %s then current_timestamp else null end,
                    updated_at=current_timestamp
                where id=%s and status='running'
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
                    done,
                    done,
                    op_id,
                ),
            ).fetchone()
        return dict(row) if row else None

    def transition_job_batch_operation(
        self, op_id: str, *, from_statuses: tuple[str, ...], to_status: str
    ) -> dict[str, Any] | None:
        """Control-plane CAS (reserved pause/cancel) and the watermark release.

        None = the row is not in one of ``from_statuses``. Terminal
        transitions stamp finished_at. (run_batches precedent.)
        """
        terminal = to_status in ("completed", "cancelled", "failed")
        with self.connect() as conn:
            row = conn.execute(
                """
                update job_batch_operations
                set status=%s, updated_at=current_timestamp,
                    finished_at=case when %s then current_timestamp else finished_at end
                where id=%s and status = any(%s)
                returning *
                """,
                (to_status, terminal, op_id, list(from_statuses)),
            ).fetchone()
        return dict(row) if row else None

    def fail_job_batch_operation(self, op_id: str, error_message: str) -> None:
        """Batch-level containment: deterministic failure marks the row failed
        from any non-terminal state."""
        with self.connect() as conn:
            conn.execute(
                """
                update job_batch_operations
                set status='failed', error_message=%s, updated_at=current_timestamp,
                    finished_at=current_timestamp
                where id=%s and status not in ('completed', 'cancelled', 'failed')
                """,
                (error_message[:1000], op_id),
            )
