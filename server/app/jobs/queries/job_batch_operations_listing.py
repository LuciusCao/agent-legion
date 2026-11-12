"""job_batch_operations listing/archive queries (M61): the task-center views
for batch ops.

Mirrors ``run_batches_listing`` (schema v100, #1157 phase3) one-for-one —
the Studio task center mixes batch-op cards with run_batches cards in the
same three views, so the semantics must match exactly:

- ``active`` — pending/running/paused, the small-by-construction set the
  badge counts (进行中按发起时间倒序、排队按提交顺序；全量，不分页).
- ``ended`` — terminal rows not yet archived: ``archived_at is null`` AND
  finished within the auto-archive window (``AUTO_ARCHIVE_DAYS``). Keyset on
  (finished_at desc, id desc) for the waterfall「加载更早的记录」.
- ``archived`` — terminal rows manually archived (``archived_at`` set) OR
  auto-archived by the query-time rule (finished before the window). Keyset
  on (coalesce(archived_at, finished_at) desc, id desc).

Auto-archive is a reading, never a write: no sweeper stamps rows on the
7-day boundary. ``archive_job_batch_operation`` is the manual arm — one
guarded UPDATE whose WHERE decides every race (cross-workspace id,
non-terminal state, double-archive), per the 多步变更纪律 (revalidate the
DECIDING state inside the mutation, not in the caller). The idempotent
replay (a repeat archive returns the current state instead of an error) is
the service layer's job; this WHERE stays the single arbitration point.

Clock semantics follow the run_batches precedent: row timestamps are stamped
by the DB clock (``current_timestamp``), the auto-archive cutoff is read by
the caller's Python clock (``services/job_batch_operations/listing.py``);
both are UTC instants on one host, and tests read their boundary clock at
execution time, never at module import (main 红 run 39561162339).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

AUTO_ARCHIVE_DAYS = 7
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# Same argument as run_batches: even the "small by construction" active set
# gets a hard cap.
ACTIVE_LIST_LIMIT = 500


class JobBatchOperationListingQueriesMixin(ConnectionQueriesMixin):
    def count_active_job_batch_operations(self, workspace_id: str) -> int:
        """The badge count: 进行中 + 排队（paused 计入进行中）。"""
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as n from job_batch_operations"
                " where workspace_id=%s and status in ('pending', 'running', 'paused')",
                (workspace_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_active_job_batch_operations(self, workspace_id: str) -> list[dict[str, Any]]:
        """All active operations: queued first (提交顺序), then in-flight
        (发起时间倒序). No pagination — the set is small by construction."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from job_batch_operations
                where workspace_id=%s and status in ('pending', 'running', 'paused')
                order by
                  case when status = 'pending' then 1 else 0 end,
                  case when status = 'pending' then created_at end asc,
                  case when status <> 'pending' then created_at end desc
                limit %s
                """,
                (workspace_id, ACTIVE_LIST_LIMIT),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_ended_job_batch_operations(
        self,
        workspace_id: str,
        *,
        auto_archive_cutoff: Any,
        cursor: tuple[Any, str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Terminal, unarchived, still inside the auto-archive window.

        Keyset (finished_at desc, id desc); rows without finished_at
        (unreachable by the state machine, kept defensive) sort last and end
        the paging chain (the caller emits no cursor for them).
        """
        keyset = ""
        params: list[Any] = [workspace_id, auto_archive_cutoff]
        if cursor is not None:
            keyset = "and (finished_at < %s or (finished_at = %s and id < %s))"
            params += [cursor[0], cursor[0], cursor[1]]
        params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select * from job_batch_operations
                where workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and archived_at is null
                  and (finished_at is null or finished_at >= %s)
                  {keyset}
                order by finished_at desc nulls last, id desc
                limit %s
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_archived_job_batch_operations(
        self,
        workspace_id: str,
        *,
        auto_archive_cutoff: Any,
        cursor: tuple[Any, str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Manually archived OR auto-archived (finished before the window).

        Keyset (coalesce(archived_at, finished_at) desc, id desc) — the same
        ordering the archived view renders (归档时间倒序).
        """
        keyset = ""
        params: list[Any] = [workspace_id, auto_archive_cutoff]
        if cursor is not None:
            keyset = (
                "and (coalesce(archived_at, finished_at) < %s"
                " or (coalesce(archived_at, finished_at) = %s and id < %s))"
            )
            params += [cursor[0], cursor[0], cursor[1]]
        params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select * from job_batch_operations
                where workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and (archived_at is not null
                       or (finished_at is not null and finished_at < %s))
                  {keyset}
                order by coalesce(archived_at, finished_at) desc, id desc
                limit %s
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def summarize_job_batch_operation_reasons_many(
        self, op_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        """reason_code breakdown for a whole listing page in ONE grouped
        query — the per-row ``summarize_job_batch_operation_reasons`` would
        be an N+1 on every page (the status poll keeps the single-op arm)."""
        if not op_ids:
            return {}
        with self._connect_read() as conn:
            rows = conn.execute(
                "select op_id, coalesce(reason_code, '') as reason_code, count(*) as n"
                " from job_batch_operation_results"
                " where op_id = any(%s) and status <> 'succeeded' group by op_id, reason_code",
                (op_ids,),
            ).fetchall()
        summary: dict[str, dict[str, int]] = {}
        for row in rows:
            summary.setdefault(str(row["op_id"]), {})[str(row["reason_code"])] = int(row["n"])
        return summary

    def archive_job_batch_operation(self, op_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Manual archive: one guarded UPDATE; None on every race.

        The WHERE clause is the whole arbitration — terminal state, workspace
        binding, and not-yet-archived are revalidated inside the mutation, so
        a concurrent archive is a clean miss, never a half-applied state. The
        service layer turns a miss on an already-archived row into the
        idempotent replay (returning the current state).
        """
        with self.connect() as conn:
            row = conn.execute(
                """
                update job_batch_operations
                set archived_at=current_timestamp, updated_at=current_timestamp
                where id=%s and workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and archived_at is null
                returning *
                """,
                (op_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_expired_archived_job_batch_operations(
        self, *, manual_cutoff: Any, auto_cutoff: Any, limit: int
    ) -> int:
        """Retention sweep arm (M61): archived operations past the 90-day
        archive retention (manual: archived_at; auto: finished_at already
        past AUTO_ARCHIVE_DAYS + 90d). One bounded batch per call, each in
        its own short transaction; the caller loops until a short batch
        falls below ``limit``. The per-job results child rows cascade
        (job_batch_operation_results FK on delete cascade)."""
        with self.write() as conn:
            return int(
                conn.execute(
                    """
                    delete from job_batch_operations
                    where id in (
                      select id from job_batch_operations
                      where status in ('completed', 'failed', 'cancelled')
                        and ((archived_at is not null and archived_at < %s)
                          or (archived_at is null
                              and finished_at is not null and finished_at < %s))
                      limit %s
                    )
                    """,
                    (manual_cutoff, auto_cutoff, limit),
                ).rowcount
            )

    def sweep_expired_archived_job_batch_operations(
        self, *, manual_cutoff: Any, auto_cutoff: Any, batch_size: int
    ) -> int:
        """Loop ``delete_expired_archived_job_batch_operations`` to
        exhaustion (the run_batches precedent: the removal itself is the
        progress; an interrupt resumes on the next pass)."""
        deleted = 0
        while True:
            batch = self.delete_expired_archived_job_batch_operations(
                manual_cutoff=manual_cutoff, auto_cutoff=auto_cutoff, limit=batch_size
            )
            deleted += batch
            if batch < batch_size:
                return deleted


def job_batch_operation_listing_queries_from_dsn(
    dsn: str,
) -> JobBatchOperationListingQueriesMixin:
    """Bare-DSN adapter mirroring ``run_batch_listing_queries_from_dsn``:
    the retention sweep calls the delete arm from contexts that hold a plain
    DSN, not a JobQueries facade."""
    queries = JobBatchOperationListingQueriesMixin.__new__(JobBatchOperationListingQueriesMixin)
    queries._path = dsn  # data-layer-private field (see queries/base.py)
    return queries
