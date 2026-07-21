from __future__ import annotations

from collections import deque
from threading import Lock

from server.app.db.retry import retried_on_database_conflict
from server.app.db.transaction import write_transaction
from server.app.job_event_models import CompactedJobEvents, JobEvent, JobEventKind


class JobEventBuffer:
    def __init__(self, db_path: str | None = None, max_events: int = 10000) -> None:
        self._db_path = db_path
        self._max_events = max_events
        self._events: deque[JobEvent] = deque()
        self._resync_workspace_ids: set[str] = set()
        self._revision = 0
        self._lock = Lock()

    def _next_revision(self) -> int:
        if self._db_path is None:
            self._revision += 1
            return self._revision
        self._revision = self._bump_seq(self._db_path)  # drain_compacted 的 latest_revision 上界
        return self._revision

    @retried_on_database_conflict
    def _bump_seq(self, db_path: str) -> int:
        with write_transaction(db_path) as conn:
            row = conn.execute(
                "update job_event_seq set value = value + 1 where id = 1 returning value"
            ).fetchone()
        if row is None:
            raise RuntimeError("job_event_seq singleton row is missing")
        return int(row["value"])

    def record_job_updated(self, workspace_id: str, job_id: str) -> int:
        return self.record(workspace_id, job_id, "updated")

    def record_job_created(self, workspace_id: str, job_id: str) -> int:
        return self.record(workspace_id, job_id, "created")

    def record_jobs_created(self, workspace_id: str, job_ids: list[str]) -> int:
        if not job_ids:
            return self._revision
        revision = self._next_revision()
        with self._lock:
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
        revision = self._next_revision()  # DB 发号在锁外，避免持锁等 IO
        with self._lock:
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
            latest_revision = self._revision

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
