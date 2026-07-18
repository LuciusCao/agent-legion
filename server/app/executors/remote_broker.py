from __future__ import annotations

import json
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.db.connection import connect_sqlite
from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors.models import ExecutionStatus


@dataclass(frozen=True)
class RemoteExecutionPayload:
    execution_id: str
    lease_id: str
    job_id: str
    node_key: str
    capability: str
    bundle_name: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RemoteClaim:
    execution_id: str
    job_id: str
    node_key: str
    capability: str
    bundle_url: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RemoteOutcome:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    skill_version: str = ""
    result_archive_name: str = ""


@dataclass
class _Entry:
    payload: RemoteExecutionPayload
    state: str = "queued"  # queued | claimed | done
    worker_id: str = ""
    last_heartbeat_at: datetime | None = None
    requeue_count: int = 0
    outcome: RemoteOutcome | None = None
    done_event: threading.Event = field(default_factory=threading.Event)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RemoteExecutionBroker:
    """In-memory remote execution queue with a sqlite-backed worker registry.

    One instance lives in the FastAPI process, shared by the RemoteExecutor
    (producer/blocking consumer) and the /api/remote routes (worker-facing).
    """

    def __init__(
        self,
        db_path: Path,
        bundle_dir: Path,
        *,
        claim_timeout_seconds: float = 120.0,
        requeue_limit: int = 3,
        time_source: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = db_path
        self.bundle_dir = bundle_dir
        self._claim_timeout = timedelta(seconds=claim_timeout_seconds)
        self._requeue_limit = requeue_limit
        self._now = time_source or _utcnow
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    # ---- executor-facing ----

    def submit(self, payload: RemoteExecutionPayload) -> None:
        with self._lock:
            if payload.execution_id in self._entries:
                raise ValueError(f"duplicate remote execution {payload.execution_id!r}")
            self._entries[payload.execution_id] = _Entry(payload=payload)

    def wait_result(self, execution_id: str, poll_seconds: float = 0.2) -> RemoteOutcome:
        with self._lock:
            entry = self._entries[execution_id]
        while not entry.done_event.wait(timeout=poll_seconds):
            # Sweep here too, so a lost worker fails even when nobody else polls.
            with self._lock:
                self._sweep_locked()
        assert entry.outcome is not None
        return entry.outcome

    def cancel(self, execution_id: str) -> None:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or entry.state == "done":
                return
            outcome = RemoteOutcome(
                status="cancelled", exit_code=-1, error_message="execution was cancelled"
            )
            self._finish(entry, outcome)

    # ---- worker-facing (called from routes) ----

    def dequeue(self, worker_id: str, capabilities: Collection[str]) -> RemoteClaim | None:
        with self._lock:
            self._sweep_locked()
            for entry in self._entries.values():
                if entry.state != "queued" or entry.payload.capability not in capabilities:
                    continue
                entry.state = "claimed"
                entry.worker_id = worker_id
                entry.last_heartbeat_at = self._now()
                return RemoteClaim(
                    execution_id=entry.payload.execution_id,
                    job_id=entry.payload.job_id,
                    node_key=entry.payload.node_key,
                    capability=entry.payload.capability,
                    bundle_url=f"/api/remote/executions/{entry.payload.execution_id}/bundle",
                    manifest=entry.payload.manifest,
                )
            return None

    def heartbeat(self, execution_id: str, worker_id: str) -> bool:
        with self._lock:
            self._sweep_locked()
            entry = self._entries.get(execution_id)
            if entry is None or entry.state != "claimed" or entry.worker_id != worker_id:
                return False
            entry.last_heartbeat_at = self._now()
            return True

    def complete(self, execution_id: str, worker_id: str, outcome: RemoteOutcome) -> bool:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or entry.state != "claimed" or entry.worker_id != worker_id:
                return False
            self._finish(entry, outcome)
            return True

    def complete_with_archive(
        self, execution_id: str, worker_id: str, outcome: RemoteOutcome, staging_path: Path
    ) -> bool:
        """Validate the claim, publish the archive at its final name, then finish — atomically."""
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or entry.state != "claimed" or entry.worker_id != worker_id:
                return False
            staging_path.replace(self.bundle_dir / outcome.result_archive_name)
            self._finish(entry, outcome)
            return True

    def bundle_name_for(self, execution_id: str, worker_id: str) -> str | None:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or entry.state != "claimed" or entry.worker_id != worker_id:
                return None
            return entry.payload.bundle_name

    # ---- worker registry (sqlite) ----

    def register_worker(
        self, worker_id: str, name: str, capabilities: list[str], slots: int
    ) -> None:
        now = _sqlite_timestamp(self._now())
        conn = connect_sqlite(self._db_path)
        try:
            conn.execute(
                "insert into remote_workers"
                " (worker_id, name, capabilities_json, slots, registered_at, last_seen_at)"
                " values (?, ?, ?, ?, ?, ?)"
                " on conflict(worker_id) do update set"
                "   name = excluded.name,"
                "   capabilities_json = excluded.capabilities_json,"
                "   slots = excluded.slots,"
                "   last_seen_at = excluded.last_seen_at",
                (worker_id, name, json.dumps(capabilities), slots, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def touch_worker(self, worker_id: str) -> None:
        conn = connect_sqlite(self._db_path)
        try:
            conn.execute(
                "update remote_workers set last_seen_at = ? where worker_id = ?",
                (_sqlite_timestamp(self._now()), worker_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_workers(self) -> list[dict[str, Any]]:
        conn = connect_sqlite(self._db_path)
        try:
            rows = conn.execute(
                "select worker_id, name, capabilities_json, slots, registered_at, last_seen_at"
                " from remote_workers order by worker_id"
            ).fetchall()
            return [
                {
                    "worker_id": row["worker_id"],
                    "name": row["name"],
                    "capabilities": json.loads(row["capabilities_json"]),
                    "slots": row["slots"],
                    "registered_at": row["registered_at"],
                    "last_seen_at": row["last_seen_at"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    # ---- internals ----

    def _finish(self, entry: _Entry, outcome: RemoteOutcome) -> None:
        entry.state = "done"
        entry.outcome = outcome
        entry.done_event.set()

    def _sweep_locked(self) -> None:
        now = self._now()
        for entry in self._entries.values():
            if entry.state != "claimed" or entry.last_heartbeat_at is None:
                continue
            if now - entry.last_heartbeat_at <= self._claim_timeout:
                continue
            entry.requeue_count += 1
            if entry.requeue_count > self._requeue_limit:
                self._finish(
                    entry,
                    RemoteOutcome(
                        status="failed",
                        exit_code=1,
                        error_message=(
                            f"remote execution lost its worker {entry.requeue_count} times;"
                            " requeue limit exceeded"
                        ),
                    ),
                )
            else:
                entry.state = "queued"
                entry.worker_id = ""
                entry.last_heartbeat_at = None
