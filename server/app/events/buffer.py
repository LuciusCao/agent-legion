from __future__ import annotations

from collections import deque
from threading import Lock

from server.app.db.dialect import ConnectSource
from server.app.db.retry import with_database_conflict_retry
from server.app.db.transaction import write_transaction
from server.app.events.models import CompactedJobEvents, JobEvent, JobEventKind

SEGMENT_SIZE = 1024
"""Revisions handed out per ``job_event_seq`` bump (#353).

Segmented allocation: each bump advances the singleton row by a whole
segment (``value = value + SEGMENT_SIZE``) and the segment is spent from an
in-process counter, so row-lock contention on wavefront boundaries shrinks
by the segment size. 1024 sits inside the recommended 512–2048 band: at the
~dozens-of-writes/sec baseline a segment lasts long enough to ride out a
wavefront, while the worst-case restart waste and the multi-replica
inter-segment reorder window both stay ≤ 1024 revisions. Revisions are
monotonic, not dense: unused tail numbers of an exhausted process's last
segment are simply skipped (accepted trade-off — every consumer treats the
revision as a watermark, never a dense index)."""


class JobEventBuffer:
    """In-memory event buffer; ``db_path`` (the JobQueries facade in
    production wiring, a bare DSN in tests, None for the legacy in-memory
    mode) only feeds the revision sequence bump — BOUNDARY-DATA-001, #187."""

    def __init__(self, db_path: ConnectSource | None = None, max_events: int = 10000) -> None:
        self._db_path = db_path
        self._max_events = max_events
        self._events: deque[JobEvent] = deque()
        self._resync_workspace_ids: set[str] = set()
        self._revision = 0
        # #353 segmented allocation state. ``_segment_end`` is the inclusive
        # last revision of the DB-granted segment (0 = nothing granted yet);
        # both fields are only touched under ``self._lock``.
        self._segment_end = 0
        self._lock = Lock()

    def current_revision(self) -> int:
        """Return the latest issued revision (thread-safe read)."""
        with self._lock:
            return self._revision

    def _next_revision_locked(self) -> int:
        """Advance the global revision. Caller must hold ``self._lock`` so the
        deque append order always matches revision order and ``self._revision``
        never regresses under concurrent recorders. Segmented allocation (#353):
        the ``job_event_seq`` singleton row is bumped once per SEGMENT_SIZE
        revisions; the segment tail is spent from memory. Segments are handed
        out monotonically by the DB row, and a single process spends its own
        segment in order, so issued revisions stay globally monotonic across
        segments and restarts (a fresh instance pulls the next segment from
        the already-advanced row). Single-process deployment assumption: with
        multiple replicas each holding a different segment, the
        inter-segment publish reorder window is ≤ SEGMENT_SIZE — the SSE
        consumers only compare revisions as a watermark (stale patches are
        dropped), so they tolerate reordering, only the resync heuristics
        would need revisiting."""
        if self._db_path is None:
            self._revision += 1
            return self._revision
        if self._revision >= self._segment_end:
            # Segment exhausted: take the next [value+1, value+SEGMENT_SIZE]
            # window from the singleton row in one UPDATE. The row's value
            # after the bump is the inclusive end of this process's window;
            # the start is end-SEGMENT_SIZE+1 — never 0-based, because other
            # processes (or a previous incarnation of this one) may already
            # have spent earlier segments, so a fresh instance must resume
            # counting from the DB watermark, not from zero.
            end = self._bump_seq(self._db_path, SEGMENT_SIZE)
            self._segment_end = end
            self._revision = max(self._revision, end - SEGMENT_SIZE)
        self._revision += 1
        return self._revision

    @with_database_conflict_retry
    def _bump_seq(self, db_path: str, count: int = 1) -> int:
        with write_transaction(db_path) as conn:
            row = conn.execute(
                "update job_event_seq set value = value + %s where id = 1 returning value",
                (count,),
            ).fetchone()
        if row is None:
            raise RuntimeError("job_event_seq singleton row is missing")
        return int(row["value"])

    def record_job_updated(self, workspace_id: str, job_id: str) -> int:
        return self.record(workspace_id, job_id, "updated")

    def record_jobs_created(self, workspace_id: str, job_ids: list[str]) -> int:
        if not job_ids:
            return self.current_revision()
        with self._lock:
            revision = self._next_revision_locked()
            for job_id in job_ids:
                event = JobEvent(
                    revision=revision,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    kind="created",
                )
                if len(self._events) >= self._max_events:
                    dropped = self._events.popleft()
                    self._resync_workspace_ids.add(dropped.workspace_id)
                self._events.append(event)
            return revision

    def record_job_deleted(self, workspace_id: str, job_id: str) -> int:
        return self.record(workspace_id, job_id, "deleted")

    def record(self, workspace_id: str, job_id: str, kind: JobEventKind) -> int:
        with self._lock:  # 发号与入队必须在同一临界区，保证事件按 revision 有序
            revision = self._next_revision_locked()
            event = JobEvent(revision=revision, workspace_id=workspace_id, job_id=job_id, kind=kind)
            if len(self._events) >= self._max_events:
                dropped = self._events.popleft()
                self._resync_workspace_ids.add(dropped.workspace_id)
            self._events.append(event)
            return revision

    def drain(self) -> list[JobEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def drain_compacted(self) -> CompactedJobEvents:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            resync_workspace_ids = set(self._resync_workspace_ids)
            self._resync_workspace_ids.clear()
            # Stamp flushes with the max revision actually included in this
            # batch, not the global high-water mark: clients apply patches only
            # when revision > last seen, so a watermark ahead of the delivered
            # events would pin stale state until a resync.
            latest_revision = max((event.revision for event in events), default=self._revision)

        updated: dict[str, set[str]] = {}
        created: dict[str, set[str]] = {}
        deleted: dict[str, set[str]] = {}
        for event in events:
            if event.kind == "deleted":
                deleted.setdefault(event.workspace_id, set()).add(event.job_id)
                updated.get(event.workspace_id, set()).discard(event.job_id)
                created.get(event.workspace_id, set()).discard(event.job_id)
            elif event.kind == "created":
                created.setdefault(event.workspace_id, set()).add(event.job_id)
            else:
                updated.setdefault(event.workspace_id, set()).add(event.job_id)

        return CompactedJobEvents(
            latest_revision=latest_revision,
            updated_job_ids_by_workspace=updated,
            created_job_ids_by_workspace=created,
            deleted_job_ids_by_workspace=deleted,
            resync_workspace_ids=resync_workspace_ids,
        )
