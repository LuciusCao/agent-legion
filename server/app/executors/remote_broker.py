from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import asdict, astuple, dataclass, field
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
    labels_satisfy,
    sweep,
)
from server.app.executors.models import ExecutionStatus

logger = logging.getLogger(__name__)


__all__ = [
    "RemoteClaim",
    "RemoteExecutionBroker",
    "RemoteExecutionPayload",
    "RemoteOutcome",
    "labels_satisfy",
]


@dataclass(frozen=True)
class RemoteExecutionPayload:
    execution_id: str
    lease_id: str
    job_id: str
    node_key: str
    capability: str
    bundle_name: str
    manifest: dict[str, Any]
    # Rendered by the payload builder at submit time; the broker only persists
    # and forwards it, keeping the transport layer free of builder imports.
    command_spec: dict[str, Any] | None = None


@dataclass(frozen=True)
class RemoteClaim:
    execution_id: str
    job_id: str
    node_key: str
    capability: str
    bundle_url: str
    manifest: dict[str, Any]
    command_spec: dict[str, Any] | None = None


@dataclass(frozen=True)
class RemoteOutcome:
    status: ExecutionStatus
    exit_code: int
    error_message: str = ""
    command: tuple[str, ...] = ()
    skill_version: str = ""
    result_archive_name: str = ""
    worker_id: str = ""
    # name -> "sha256:<hash>" for outputs the worker uploaded as artifacts.
    output_artifacts: Mapping[str, str] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _validate_labels(labels: Mapping[str, Any]) -> None:
    """Worker labels are flat scalars only; nested objects could smuggle large
    or sensitive payloads into the registry (spec Security)."""
    for key, value in labels.items():
        if not isinstance(key, str):
            raise ValueError(f"labels keys must be strings, got {type(key).__name__}")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(
                f"labels values must be str/int/float/bool scalars, got {type(value).__name__}"
                f" for key {key!r}"
            )


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
        capability_label_requirements: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._db_path = db_path
        self.bundle_dir = bundle_dir
        self._claim_timeout = timedelta(seconds=claim_timeout_seconds)
        self._requeue_limit = requeue_limit
        self._now = time_source or _utcnow
        # capability -> requires_labels constraints evaluated at dequeue time.
        self._label_requirements = {
            cap: dict(reqs) for cap, reqs in (capability_label_requirements or {}).items()
        }
        self._done_events: dict[str, threading.Event] = {}
        self._completion_callbacks: list[Callable[[str, RemoteOutcome], None]] = []
        self._entries = EntriesView(db_path)  # legacy test handle; rows live in sqlite
        # Recover claims orphaned by a restart before serving new work.
        self.sweep_expired_claims()

    def register_completion_callback(self, callback: Callable[[str, RemoteOutcome], None]) -> None:
        """Register a callback invoked (outside the write transaction) each time
        an execution reaches a terminal state — result report, cancel, or
        requeue-limit failure. Registration is a composition-layer concern."""
        self._completion_callbacks.append(callback)

    # ---- executor-facing ----

    def submit(self, payload: RemoteExecutionPayload) -> None:
        self.sweep_expired_claims()  # Any write path also triggers the done-row cleanup.
        try:
            retry_on_sqlite_lock(lambda: self._insert_execution(payload))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"duplicate remote execution {payload.execution_id!r}") from exc

    def _insert_execution(self, payload: RemoteExecutionPayload) -> None:
        now = _sqlite_timestamp(self._now())
        # astuple 前 6 个字段与 insert 列顺序一致；manifest 与 command_spec 单独序列化。
        values = (
            *astuple(payload)[:6],
            json.dumps(payload.manifest),
            json.dumps(payload.command_spec) if payload.command_spec is not None else None,
            now,
            now,
        )
        self._write(
            "insert into remote_executions"
            " (execution_id, lease_id, job_id, node_key, capability,"
            "  bundle_name, manifest_json, command_spec_json, state, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            values,
        )

    def wait_result(self, execution_id: str, poll_seconds: float = 0.2) -> RemoteOutcome:
        event = self._done_events.setdefault(execution_id, threading.Event())
        while True:
            outcome = self._read_outcome(execution_id)
            if outcome is not None:
                return outcome
            # Sweep here too, so a lost worker fails even when nobody else polls.
            self.sweep_expired_claims()
            event.wait(timeout=poll_seconds)
            event.clear()

    def payload_for(self, execution_id: str) -> RemoteExecutionPayload | None:
        """Return the stored submission payload for an execution, in any state.

        Rows survive restarts, so completion callbacks can always rebuild the
        submission context (lease, job, manifest) even after a process bounce.
        """
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select execution_id, lease_id, job_id, node_key, capability, bundle_name,"
                " manifest_json, command_spec_json from remote_executions"
                " where execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        spec_json = row["command_spec_json"]
        return RemoteExecutionPayload(
            execution_id=row["execution_id"],
            lease_id=row["lease_id"],
            job_id=row["job_id"],
            node_key=row["node_key"],
            capability=row["capability"],
            bundle_name=row["bundle_name"],
            manifest=json.loads(row["manifest_json"]),
            command_spec=json.loads(spec_json) if spec_json else None,
        )

    def active_lease_ids(self) -> list[str]:
        """Lease ids of queued or claimed rows; renewal input for the lease sweeper."""
        with read_connection(self._db_path) as conn:
            rows = conn.execute(
                "select lease_id from remote_executions where state in ('queued', 'claimed')"
            ).fetchall()
            return [str(row["lease_id"]) for row in rows]

    def cancel(self, execution_id: str) -> None:
        outcome = RemoteOutcome(
            status="cancelled", exit_code=-1, error_message="execution was cancelled"
        )
        if cancel_execution(
            self._db_path, execution_id, asdict(outcome), _sqlite_timestamp(self._now())
        ):
            self._publish_completion(execution_id)

    # ---- worker-facing (called from routes) ----

    def dequeue(self, worker_id: str, capabilities: Collection[str]) -> RemoteClaim | None:
        if not capabilities:
            return None
        self.sweep_expired_claims()
        entry = claim_next(
            self._db_path,
            worker_id,
            capabilities,
            _sqlite_timestamp(self._now()),
            label_requirements=self._label_requirements or None,
        )
        if entry is None:
            return None
        spec_json = entry.get("command_spec_json")
        return RemoteClaim(
            execution_id=entry["execution_id"],
            job_id=entry["job_id"],
            node_key=entry["node_key"],
            capability=entry["capability"],
            bundle_url=f"/api/remote/executions/{entry['execution_id']}/bundle",
            manifest=json.loads(entry["manifest_json"]),
            command_spec=json.loads(spec_json) if spec_json else None,
        )

    def heartbeat(self, execution_id: str, worker_id: str) -> bool:
        self.sweep_expired_claims()
        return heartbeat_claim(
            self._db_path, execution_id, worker_id, _sqlite_timestamp(self._now())
        )

    def complete(self, execution_id: str, worker_id: str, outcome: RemoteOutcome) -> bool:
        finished = finish_execution(
            self._db_path, execution_id, worker_id, asdict(outcome), _sqlite_timestamp(self._now())
        )
        if finished:
            self._publish_completion(execution_id)
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
            self._publish_completion(execution_id)
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

    def update_worker_labels(self, worker_id: str, labels: Mapping[str, Any]) -> None:
        """Replace a worker's stored labels from its claim-time self-report.

        Same flat-scalar rules as token issuance (Task 4): nested values are
        rejected so the registry cannot smuggle large or sensitive payloads.
        """
        flat_labels = dict(labels)
        _validate_labels(flat_labels)
        retry_on_sqlite_lock(
            lambda: self._write(
                "update remote_workers set labels_json = ? where worker_id = ?",
                (json.dumps(flat_labels), worker_id),
            )
        )

    def list_workers(self) -> list[dict[str, Any]]:
        with read_connection(self._db_path) as conn:
            rows = conn.execute(
                "select worker_id, name, capabilities_json, slots, labels_json, registered_at,"
                " last_seen_at, revoked_at from remote_workers order by worker_id"
            ).fetchall()
            return [
                {
                    "worker_id": row["worker_id"],
                    "name": row["name"],
                    "capabilities": json.loads(row["capabilities_json"]),
                    "slots": row["slots"],
                    "labels": json.loads(row["labels_json"] or "{}"),
                    "registered_at": row["registered_at"],
                    "last_seen_at": row["last_seen_at"],
                    "revoked": row["revoked_at"] is not None,
                }
                for row in rows
            ]

    # ---- worker trust: per-worker tokens (SEC-WORKER-001) ----

    def issue_worker_token(
        self,
        worker_id: str,
        name: str,
        capabilities: list[str],
        slots: int,
        labels: Mapping[str, Any] | None = None,
    ) -> str:
        """Register/rotate a worker and return its plaintext token exactly once.

        Only ``sha256(secret)`` is persisted; the worker_id half of the token
        is the plaintext lookup key. Re-issuing rotates the hash (the old
        secret dies immediately) and clears any prior revocation — re-issuance
        is the operator's re-onboarding path and requires the management token.
        """
        # The token form is "{worker_id}.{secret}" and authentication splits on
        # the first ".", so a dotted worker_id would issue a token that can
        # never authenticate back (fail-closed, with no error anywhere).
        if "." in worker_id:
            raise ValueError(f"worker_id must not contain '.': {worker_id!r}")
        flat_labels = dict(labels or {})
        _validate_labels(flat_labels)
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        retry_on_sqlite_lock(
            lambda: self._upsert_worker_token(
                worker_id, name, capabilities, slots, flat_labels, token_hash
            )
        )
        return f"{worker_id}.{secret}"

    def _upsert_worker_token(
        self,
        worker_id: str,
        name: str,
        caps: list[str],
        slots: int,
        labels: dict[str, Any],
        token_hash: str,
    ) -> None:
        now = _sqlite_timestamp(self._now())
        self._write(
            "insert into remote_workers"
            " (worker_id, name, capabilities_json, slots, labels_json, token_hash,"
            "  registered_at, last_seen_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?) on conflict(worker_id) do update set"
            "   name = excluded.name, capabilities_json = excluded.capabilities_json,"
            "   slots = excluded.slots, labels_json = excluded.labels_json,"
            "   token_hash = excluded.token_hash, revoked_at = null,"
            "   last_seen_at = excluded.last_seen_at",
            (worker_id, name, json.dumps(caps), slots, json.dumps(labels), token_hash, now, now),
        )

    def authenticate_worker(self, token: str) -> dict[str, Any] | None:
        """Resolve ``{worker_id}.{secret}`` to the worker record, or None.

        Revoked workers and hash mismatches both return None; the caller cannot
        distinguish them (no oracle for valid worker_ids).
        """
        worker_id, sep, secret = token.partition(".")
        if not sep or not worker_id or not secret:
            return None
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select worker_id, name, capabilities_json, slots, labels_json, token_hash,"
                " revoked_at from remote_workers where worker_id = ?",
                (worker_id,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None or not row["token_hash"]:
            return None
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, row["token_hash"]):
            return None
        return {
            "worker_id": row["worker_id"],
            "name": row["name"],
            "capabilities": json.loads(row["capabilities_json"]),
            "slots": row["slots"],
            "labels": json.loads(row["labels_json"]),
        }

    def revoke_worker(self, worker_id: str) -> bool:
        """Mark a worker revoked; authentication rejects it from this commit on."""

        def _revoke() -> bool:
            with write_transaction(self._db_path) as conn:
                cursor = conn.execute(
                    "update remote_workers set revoked_at = ? where worker_id = ?",
                    (_sqlite_timestamp(self._now()), worker_id),
                )
                return cursor.rowcount > 0

        return retry_on_sqlite_lock(_revoke)

    def is_worker_revoked(self, worker_id: str) -> bool:
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select revoked_at from remote_workers where worker_id = ?", (worker_id,)
            ).fetchone()
        return row is not None and row["revoked_at"] is not None

    # ---- internals ----

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        with write_transaction(self._db_path) as conn:
            conn.execute(sql, params)

    def _signal_done(self, execution_id: str) -> None:
        event = self._done_events.get(execution_id)
        if event is not None:
            event.set()

    def _read_outcome(self, execution_id: str) -> RemoteOutcome | None:
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select outcome_json from remote_executions"
                " where execution_id = ? and state = 'done'",
                (execution_id,),
            ).fetchone()
        if row is None or row["outcome_json"] is None:
            return None
        data = json.loads(row["outcome_json"])
        data["command"] = tuple(data.get("command", []))
        return RemoteOutcome(**data)

    def _publish_completion(self, execution_id: str) -> None:
        """Signal waiters and invoke completion callbacks for a finished row.

        Every terminal write path (result report, cancel, requeue-limit
        failure) funnels here after its transaction has committed, so
        callbacks never observe uncommitted state. The broker state machine
        deduplicates terminal writes, so each execution publishes exactly
        once. Callback exceptions are logged, never propagated: a faulty
        callback cannot undo the committed completion.
        """
        self._signal_done(execution_id)
        if not self._completion_callbacks:
            return
        outcome = self._read_outcome(execution_id)
        if outcome is None:
            return
        for callback in self._completion_callbacks:
            try:
                callback(execution_id, outcome)
            except Exception:
                logger.exception("remote completion callback failed for %s", execution_id)

    def sweep_expired_claims(self) -> list[str]:
        """Requeue stale claims, fail rows past the requeue limit, reap old done rows.

        Public entry for the lease sweeper (Task 8); broker write paths call it
        inline as well. Returns execution ids that reached a terminal state here.
        """
        finished = sweep(
            self._db_path,
            now=self._now(),
            claim_timeout=self._claim_timeout,
            requeue_limit=self._requeue_limit,
        )
        for execution_id in finished:
            self._publish_completion(execution_id)
        return finished
