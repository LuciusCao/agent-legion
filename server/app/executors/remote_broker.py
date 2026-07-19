from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Collection
from dataclasses import asdict, astuple, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.db.retry import retry_on_sqlite_lock
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors._remote_queue_store import (
    EntriesView,
    bundle_name_for_claim,
    cancel_execution,
    claim_next,
    finish_execution,
    heartbeat_claim,
    sweep,
)
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
    worker_id: str = ""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RemoteExecutionBroker:
    """Sqlite-backed remote execution queue and worker registry.

    Rows in ``remote_executions`` survive restarts; claims are atomic across
    processes; ``_done_events`` is only a same-process wake-up hint.
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
        self._done_events: dict[str, threading.Event] = {}
        self._entries = EntriesView(db_path)  # legacy test handle; rows live in sqlite
        # Recover claims orphaned by a restart before serving new work.
        self._sweep()

    # ---- executor-facing ----

    def submit(self, payload: RemoteExecutionPayload) -> None:
        self._sweep()  # Any write path also triggers the done-row cleanup.
        try:
            retry_on_sqlite_lock(lambda: self._insert_execution(payload))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"duplicate remote execution {payload.execution_id!r}") from exc

    def _insert_execution(self, payload: RemoteExecutionPayload) -> None:
        now = _sqlite_timestamp(self._now())
        # astuple 前 6 个字段与 insert 列顺序一致，manifest 单独序列化。
        values = (*astuple(payload)[:6], json.dumps(payload.manifest), now, now)
        self._write(
            "insert into remote_executions"
            " (execution_id, lease_id, job_id, node_key, capability,"
            "  bundle_name, manifest_json, state, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            values,
        )

    def wait_result(self, execution_id: str, poll_seconds: float = 0.2) -> RemoteOutcome:
        event = self._done_events.setdefault(execution_id, threading.Event())
        while True:
            with read_connection(self._db_path) as conn:
                row = conn.execute(
                    "select outcome_json from remote_executions"
                    " where execution_id = ? and state = 'done'",
                    (execution_id,),
                ).fetchone()
            if row is not None and row["outcome_json"] is not None:
                data = json.loads(row["outcome_json"])
                data["command"] = tuple(data.get("command", []))
                return RemoteOutcome(**data)
            # Sweep here too, so a lost worker fails even when nobody else polls.
            self._sweep()
            event.wait(timeout=poll_seconds)
            event.clear()

    def cancel(self, execution_id: str) -> None:
        outcome = RemoteOutcome(
            status="cancelled", exit_code=-1, error_message="execution was cancelled"
        )
        if cancel_execution(
            self._db_path, execution_id, asdict(outcome), _sqlite_timestamp(self._now())
        ):
            self._signal_done(execution_id)

    # ---- worker-facing (called from routes) ----

    def dequeue(self, worker_id: str, capabilities: Collection[str]) -> RemoteClaim | None:
        if not capabilities:
            return None
        self._sweep()
        entry = claim_next(self._db_path, worker_id, capabilities, _sqlite_timestamp(self._now()))
        if entry is None:
            return None
        return RemoteClaim(
            execution_id=entry["execution_id"],
            job_id=entry["job_id"],
            node_key=entry["node_key"],
            capability=entry["capability"],
            bundle_url=f"/api/remote/executions/{entry['execution_id']}/bundle",
            manifest=json.loads(entry["manifest_json"]),
        )

    def heartbeat(self, execution_id: str, worker_id: str) -> bool:
        self._sweep()
        return heartbeat_claim(
            self._db_path, execution_id, worker_id, _sqlite_timestamp(self._now())
        )

    def complete(self, execution_id: str, worker_id: str, outcome: RemoteOutcome) -> bool:
        finished = finish_execution(
            self._db_path, execution_id, worker_id, asdict(outcome), _sqlite_timestamp(self._now())
        )
        if finished:
            self._signal_done(execution_id)
        return finished

    def complete_with_archive(
        self, execution_id: str, worker_id: str, outcome: RemoteOutcome, staging_path: Path
    ) -> bool:
        """Validate the claim, publish the archive at its final name, then finish — atomically."""

        def publish_archive() -> None:
            staging_path.replace(self.bundle_dir / outcome.result_archive_name)

        finished = finish_execution(
            self._db_path,
            execution_id,
            worker_id,
            asdict(outcome),
            _sqlite_timestamp(self._now()),
            before_update=publish_archive,
        )
        if finished:
            self._signal_done(execution_id)
        return finished

    def bundle_name_for(self, execution_id: str, worker_id: str) -> str | None:
        return bundle_name_for_claim(self._db_path, execution_id, worker_id)

    # ---- worker registry (sqlite) ----

    def register_worker(
        self, worker_id: str, name: str, capabilities: list[str], slots: int
    ) -> None:
        retry_on_sqlite_lock(lambda: self._upsert_worker(worker_id, name, capabilities, slots))

    def _upsert_worker(self, worker_id: str, name: str, caps: list[str], slots: int) -> None:
        now = _sqlite_timestamp(self._now())
        self._write(
            "insert into remote_workers"
            " (worker_id, name, capabilities_json, slots, registered_at, last_seen_at)"
            " values (?, ?, ?, ?, ?, ?) on conflict(worker_id) do update set"
            "   name = excluded.name, capabilities_json = excluded.capabilities_json,"
            "   slots = excluded.slots, last_seen_at = excluded.last_seen_at",
            (worker_id, name, json.dumps(caps), slots, now, now),
        )

    def touch_worker(self, worker_id: str) -> None:
        retry_on_sqlite_lock(
            lambda: self._write(
                "update remote_workers set last_seen_at = ? where worker_id = ?",
                (_sqlite_timestamp(self._now()), worker_id),
            )
        )

    def list_workers(self) -> list[dict[str, Any]]:
        with read_connection(self._db_path) as conn:
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

    # ---- internals ----

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        with write_transaction(self._db_path) as conn:
            conn.execute(sql, params)

    def _signal_done(self, execution_id: str) -> None:
        event = self._done_events.get(execution_id)
        if event is not None:
            event.set()

    def _sweep(self) -> None:
        finished = sweep(
            self._db_path,
            now=self._now(),
            claim_timeout=self._claim_timeout,
            requeue_limit=self._requeue_limit,
        )
        for execution_id in finished:
            self._signal_done(execution_id)
