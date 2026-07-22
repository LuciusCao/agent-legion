from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

# Worker-supplied registration fields are scheduling inputs from a
# management-token holder, not trusted identity: keep them bounded so a
# malformed or hostile registration cannot bloat rows or smuggle control
# characters into logs/UI. worker_id additionally joins the token format
# "<worker_id>.<secret>", so '.' must stay excluded.
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_NAME_LENGTH = 128
_MAX_LABELS = 32
_MAX_LABEL_KEY_LENGTH = 64
_MAX_LABEL_VALUE_LENGTH = 256
_MAX_CONCURRENCY = 1024


def _validate_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    if len(labels) > _MAX_LABELS:
        raise ValueError(f"worker labels are capped at {_MAX_LABELS} entries")
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not key:
            raise ValueError("worker label keys must be non-empty strings")
        if len(key) > _MAX_LABEL_KEY_LENGTH:
            raise ValueError(
                f"worker label key {key[:16]!r}... exceeds {_MAX_LABEL_KEY_LENGTH} chars"
            )
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"worker label {key!r} must have a scalar value")
        text = str(value)
        if len(text) > _MAX_LABEL_VALUE_LENGTH:
            raise ValueError(f"worker label {key!r} value exceeds {_MAX_LABEL_VALUE_LENGTH} chars")
        normalized[key] = text
    return normalized


class AgentWorkerRegistry:
    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self.database_dsn = database_dsn

    def issue_token(
        self,
        *,
        worker_id: str,
        name: str,
        runtimes: list[str],
        max_concurrency: int,
        labels: Mapping[str, Any] | None = None,
        protocol_version: int = 1,
        image_version: str = "",
    ) -> str:
        # image_version is accepted for forward compatibility but not stored:
        # the agent_workers table has no column for it yet.
        if not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker_id must be 1-64 chars of [A-Za-z0-9_-] starting alphanumeric")
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(f"worker name exceeds {_MAX_NAME_LENGTH} chars")
        if not 0 < max_concurrency <= _MAX_CONCURRENCY:
            raise ValueError(f"max_concurrency must be between 1 and {_MAX_CONCURRENCY}")
        normalized_runtimes = sorted(set(runtimes))
        if not normalized_runtimes or any(
            runtime not in {"pi", "openclaw"} for runtime in normalized_runtimes
        ):
            raise ValueError("runtimes must contain pi and/or openclaw")
        normalized_labels = _validate_labels(labels or {})
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        now = datetime.now(UTC)
        with write_transaction(self.database_dsn) as conn:
            conn.execute(
                """
                insert into agent_workers(
                  worker_id, name, runtimes_json, max_concurrency, labels_json,
                  protocol_version, token_hash, registered_at, last_seen_at, revoked_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, null)
                on conflict(worker_id) do update set
                  name=excluded.name,
                  runtimes_json=excluded.runtimes_json,
                  max_concurrency=excluded.max_concurrency,
                  labels_json=excluded.labels_json,
                  protocol_version=excluded.protocol_version,
                  token_hash=excluded.token_hash,
                  last_seen_at=excluded.last_seen_at,
                  revoked_at=null
                """,
                (
                    worker_id,
                    name,
                    json.dumps(normalized_runtimes),
                    max_concurrency,
                    json.dumps(normalized_labels, sort_keys=True),
                    protocol_version,
                    token_hash,
                    now,
                    now,
                ),
            )
        return f"{worker_id}.{secret}"

    def authenticate(self, token: str) -> dict[str, Any] | None:
        worker_id, separator, secret = token.partition(".")
        if not separator or not worker_id or not secret:
            return None
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select * from agent_workers where worker_id=?", (worker_id,)
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not hmac.compare_digest(digest, row["token_hash"]):
            return None
        return _worker_payload(row)

    def list_workers(self) -> list[dict[str, Any]]:
        with read_connection(self.database_dsn) as conn:
            rows = conn.execute("select * from agent_workers order by worker_id").fetchall()
        return [_worker_payload(row) for row in rows]

    def revoke(self, worker_id: str) -> bool:
        with write_transaction(self.database_dsn) as conn:
            result = conn.execute(
                "update agent_workers set revoked_at=current_timestamp where worker_id=?",
                (worker_id,),
            )
            return result.rowcount > 0


def _worker_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "runtimes": json.loads(row["runtimes_json"]),
        "max_concurrency": int(row["max_concurrency"]),
        "labels": json.loads(row["labels_json"]),
        "protocol_version": int(row["protocol_version"]),
        "registered_at": row["registered_at"],
        "last_seen_at": row["last_seen_at"],
        "revoked": row["revoked_at"] is not None,
    }
