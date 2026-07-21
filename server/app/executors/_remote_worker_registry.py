"""PostgreSQL worker registry and per-worker token store for remote execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from server.app.db.retry import retry_on_database_conflict
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_transactions import _database_timestamp


def _validate_labels(labels: Mapping[str, Any]) -> None:
    for key, value in labels.items():
        if not isinstance(key, str):
            raise ValueError(f"labels keys must be strings, got {type(key).__name__}")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(
                f"labels values must be str/int/float/bool scalars, got {type(value).__name__}"
                f" for key {key!r}"
            )


class WorkerRegistryStore:
    def __init__(self, db_path: str, now: Callable[[], datetime]) -> None:
        self._db_path = db_path
        self._now = now

    def register(self, worker_id: str, name: str, capabilities: list[str], slots: int) -> None:
        def upsert() -> None:
            now = _database_timestamp(self._now())
            self._write(
                "insert into remote_workers"
                " (worker_id, name, capabilities_json, slots, registered_at, last_seen_at)"
                " values (?, ?, ?, ?, ?, ?) on conflict(worker_id) do update set"
                " name=excluded.name, capabilities_json=excluded.capabilities_json,"
                " slots=excluded.slots, last_seen_at=excluded.last_seen_at",
                (worker_id, name, json.dumps(capabilities), slots, now, now),
            )

        retry_on_database_conflict(upsert)

    def touch(self, worker_id: str) -> None:
        retry_on_database_conflict(
            lambda: self._write(
                "update remote_workers set last_seen_at=? where worker_id=?",
                (_database_timestamp(self._now()), worker_id),
            )
        )

    def update_labels(self, worker_id: str, labels: Mapping[str, Any]) -> None:
        flat_labels = dict(labels)
        _validate_labels(flat_labels)
        retry_on_database_conflict(
            lambda: self._write(
                "update remote_workers set labels_json=? where worker_id=?",
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

    def issue_token(
        self,
        worker_id: str,
        name: str,
        capabilities: list[str],
        slots: int,
        labels: Mapping[str, Any] | None = None,
    ) -> str:
        if "." in worker_id:
            raise ValueError(f"worker_id must not contain '.': {worker_id!r}")
        flat_labels = dict(labels or {})
        _validate_labels(flat_labels)
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        def upsert() -> None:
            now = _database_timestamp(self._now())
            self._write(
                "insert into remote_workers"
                " (worker_id, name, capabilities_json, slots, labels_json, token_hash,"
                " registered_at, last_seen_at) values (?, ?, ?, ?, ?, ?, ?, ?)"
                " on conflict(worker_id) do update set name=excluded.name,"
                " capabilities_json=excluded.capabilities_json, slots=excluded.slots,"
                " labels_json=excluded.labels_json, token_hash=excluded.token_hash,"
                " revoked_at=null, last_seen_at=excluded.last_seen_at",
                (
                    worker_id,
                    name,
                    json.dumps(capabilities),
                    slots,
                    json.dumps(flat_labels),
                    token_hash,
                    now,
                    now,
                ),
            )

        retry_on_database_conflict(upsert)
        return f"{worker_id}.{secret}"

    def authenticate(self, token: str) -> dict[str, Any] | None:
        worker_id, sep, secret = token.partition(".")
        if not sep or not worker_id or not secret:
            return None
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select worker_id, name, capabilities_json, slots, labels_json, token_hash,"
                " revoked_at from remote_workers where worker_id=?",
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

    def revoke(self, worker_id: str) -> bool:
        def revoke_once() -> bool:
            with write_transaction(self._db_path) as conn:
                cursor = conn.execute(
                    "update remote_workers set revoked_at=? where worker_id=?",
                    (_database_timestamp(self._now()), worker_id),
                )
                return cursor.rowcount > 0

        return retry_on_database_conflict(revoke_once)

    def is_revoked(self, worker_id: str) -> bool:
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                "select revoked_at from remote_workers where worker_id=?",
                (worker_id,),
            ).fetchone()
        return row is not None and row["revoked_at"] is not None

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        with write_transaction(self._db_path) as conn:
            conn.execute(sql, params)
