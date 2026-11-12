"""run_batches listing/archive queries (#1157 phase3): the task center views.

The Studio task center reads run_batches in three views (design:
docs/architecture/runs-async-mode.md §6):

- ``active`` — pending/running/paused, the small-by-construction set the
  badge counts (进行中按发起时间倒序、排队按提交顺序；全量，不分页).
- ``ended`` — terminal rows not yet archived: ``archived_at is null`` AND
  finished within the auto-archive window (``AUTO_ARCHIVE_DAYS``). Keyset on
  (finished_at desc, run_id desc) for the waterfall「加载更早的记录」.
- ``archived`` — terminal rows manually archived (``archived_at`` set) OR
  auto-archived by the query-time rule (finished before the window). Keyset
  on (coalesce(archived_at, finished_at) desc, run_id desc).

Auto-archive is a reading, never a write: no sweeper stamps rows on the
7-day boundary. ``archive_run_batch`` is the manual arm — one guarded
UPDATE whose WHERE decides every race (cross-workspace id, non-terminal
state, double-archive), per the 多步变更纪律 (revalidate the DECIDING state
inside the mutation, not in the caller).

Clock semantics: row timestamps (``finished_at`` / ``archived_at``) are
stamped by the DB clock (``current_timestamp``), while the auto-archive
cutoff is read by the caller's Python clock (``datetime.now(UTC)`` in
``services/run_batch_listing.py``). Both are UTC instants and prod runs
app+DB on one host, so the two clocks are the same wall clock; the 7-day
reading tolerates any residual sub-second skew by construction. Tests must
read their boundary clock at execution time, never at module import (CI
xdist collects long before it executes — main 红 run 39561162339).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

AUTO_ARCHIVE_DAYS = 7
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# Paused batches accumulate until the operator cancels them, so even the
# "small by construction" active set gets a hard cap.
ACTIVE_LIST_LIMIT = 500


class RunBatchListingQueriesMixin(ConnectionQueriesMixin):
    def count_active_run_batches(self, workspace_id: str) -> int:
        """The AppBar badge count: 进行中 + 排队（paused 计入进行中）。"""
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as n from run_batches"
                " where workspace_id=%s and status in ('pending', 'running', 'paused')",
                (workspace_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_active_run_batches(self, workspace_id: str) -> list[dict[str, Any]]:
        """All active batches: feeding first (发起时间倒序), then queued
        (提交顺序). No pagination — the set is small by construction."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from run_batches
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

    def list_ended_run_batches(
        self,
        workspace_id: str,
        *,
        auto_archive_cutoff: Any,
        cursor: tuple[Any, str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Terminal, unarchived, still inside the auto-archive window.

        Keyset (finished_at desc, run_id desc); rows without finished_at
        (unreachable by the state machine, kept defensive) sort last and end
        the paging chain (the caller emits no cursor for them).
        """
        keyset = ""
        params: list[Any] = [workspace_id, auto_archive_cutoff]
        if cursor is not None:
            keyset = "and (finished_at < %s or (finished_at = %s and run_id < %s))"
            params += [cursor[0], cursor[0], cursor[1]]
        params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select * from run_batches
                where workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and archived_at is null
                  and (finished_at is null or finished_at >= %s)
                  {keyset}
                order by finished_at desc nulls last, run_id desc
                limit %s
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_archived_run_batches(
        self,
        workspace_id: str,
        *,
        auto_archive_cutoff: Any,
        cursor: tuple[Any, str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Manually archived OR auto-archived (finished before the window).

        Keyset (coalesce(archived_at, finished_at) desc, run_id desc) — the
        same ordering the archived view renders (归档时间倒序).
        """
        keyset = ""
        params: list[Any] = [workspace_id, auto_archive_cutoff]
        if cursor is not None:
            keyset = (
                "and (coalesce(archived_at, finished_at) < %s"
                " or (coalesce(archived_at, finished_at) = %s and run_id < %s))"
            )
            params += [cursor[0], cursor[0], cursor[1]]
        params.append(limit)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select * from run_batches
                where workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and (archived_at is not null
                       or (finished_at is not null and finished_at < %s))
                  {keyset}
                order by coalesce(archived_at, finished_at) desc, run_id desc
                limit %s
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def archive_run_batch(self, run_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Manual archive: one guarded UPDATE; None on every race.

        The WHERE clause is the whole arbitration — terminal state, workspace
        binding, and not-yet-archived are revalidated inside the mutation, so
        a double-click or a concurrent archive is a clean miss (the caller
        maps it to 409), never a half-applied state.
        """
        with self.connect() as conn:
            row = conn.execute(
                """
                update run_batches
                set archived_at=current_timestamp, updated_at=current_timestamp
                where run_id=%s and workspace_id=%s
                  and status in ('completed', 'failed', 'cancelled')
                  and archived_at is null
                returning *
                """,
                (run_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_expired_archived_run_batches(
        self, *, manual_cutoff: Any, auto_cutoff: Any, limit: int
    ) -> int:
        """Retention sweep arm (#1157): archived batches past the 90-day
        archive retention (manual: archived_at; auto: finished_at already
        past AUTO_ARCHIVE_DAYS + 90d). One bounded batch per call, each in
        its own short transaction; the caller loops until a short batch
        falls below ``limit``. Only terminal rows can carry archived_at /
        sit past the auto cutoff (defensive status guard included), so the
        feeder never meets this removal.
        """
        with self.write() as conn:
            return int(
                conn.execute(
                    """
                    delete from run_batches
                    where run_id in (
                      select run_id from run_batches
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

    def sweep_expired_archived_run_batches(
        self, *, manual_cutoff: Any, auto_cutoff: Any, batch_size: int
    ) -> int:
        """Loop ``delete_expired_archived_run_batches`` to exhaustion.

        The removal itself is the progress, so no persisted cursor is
        needed; an interrupt simply resumes on the next pass (each batch
        commits independently).
        """
        deleted = 0
        while True:
            batch = self.delete_expired_archived_run_batches(
                manual_cutoff=manual_cutoff, auto_cutoff=auto_cutoff, limit=batch_size
            )
            deleted += batch
            if batch < batch_size:
                return deleted


def run_batch_listing_queries_from_dsn(dsn: str) -> RunBatchListingQueriesMixin:
    """Bare-DSN adapter mirroring ``execution_retention_queries_from_dsn``
    (#187 ConnectSource): the retention sweep calls the delete arm from
    contexts that hold a plain DSN, not a JobQueries facade."""
    queries = RunBatchListingQueriesMixin.__new__(RunBatchListingQueriesMixin)
    queries._path = dsn  # data-layer-private field (see queries/base.py)
    return queries
