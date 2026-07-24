from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_worker_declarations import normalize_labels, normalize_worker_declarations
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

# Worker-supplied registration fields are scheduling inputs from a
# management-token holder, not trusted identity: keep them bounded so a
# malformed or hostile registration cannot bloat rows or smuggle control
# characters into logs/UI. worker_id additionally joins the token format
# "<worker_id>.<secret>", so '.' must stay excluded.
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_NAME_LENGTH = 128
_MAX_CONCURRENCY = 1024
_MAX_TOKEN_LABEL_LENGTH = 128
# A Worker is "online" while its last authenticated call (claim poll every few
# seconds, or an execution heartbeat) is fresher than this threshold.
_ONLINE_THRESHOLD_SECONDS = 30


class AgentWorkerRegistry:
    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self.database_dsn = database_dsn

    def issue_token(
        self,
        *,
        worker_id: str,
        name: str,
        runtimes: list[str],
        capabilities: Sequence[str] | None = None,
        models: Sequence[Mapping[str, Any]] | None = None,
        max_concurrency: int,
        labels: Mapping[str, Any] | None = None,
        protocol_version: int = 1,
        image_version: str = "",
        allowed_workspaces: Sequence[str] | None = None,
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
        normalized_labels = normalize_labels(labels or {})
        # None is kept as an internal compatibility mode for older direct
        # registry callers; the HTTP contract always supplies explicit lists.
        normalized_capabilities, normalized_models = normalize_worker_declarations(
            capabilities, models
        )
        # The workspace scope is ALWAYS resolved server-side from the presented
        # registration credential (route layer), never from Worker fields:
        # global register token -> [] (all workspaces, the pre-v7 behavior);
        # scoped token -> [workspace_id]. Re-registering rotates the token AND
        # refreshes the scope from the current credential — revoking a scoped
        # register token therefore only bites at the next re-registration, it
        # does not narrow an already-registered Worker's stored scope.
        scope = sorted({str(workspace) for workspace in (allowed_workspaces or [])})
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        now = datetime.now(UTC)
        with write_transaction(self.database_dsn) as conn:
            for workspace in scope:
                exists = conn.execute(
                    "select 1 from workspaces where id=?", (workspace,)
                ).fetchone()
                if exists is None:
                    raise ValueError(f"workspace {workspace!r} does not exist")
            conn.execute(
                """
                insert into agent_workers(
                  worker_id, name, runtimes_json, capabilities_json, models_json,
                  max_concurrency, labels_json,
                  protocol_version, token_hash, allowed_workspaces_json,
                  registered_at, last_seen_at, revoked_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null)
                on conflict(worker_id) do update set
                  name=excluded.name,
                  runtimes_json=excluded.runtimes_json,
                  capabilities_json=excluded.capabilities_json,
                  models_json=excluded.models_json,
                  max_concurrency=excluded.max_concurrency,
                  labels_json=excluded.labels_json,
                  protocol_version=excluded.protocol_version,
                  token_hash=excluded.token_hash,
                  allowed_workspaces_json=excluded.allowed_workspaces_json,
                  last_seen_at=excluded.last_seen_at,
                  revoked_at=null
                """,
                (
                    worker_id,
                    name,
                    json.dumps(normalized_runtimes),
                    json.dumps(normalized_capabilities),
                    json.dumps(normalized_models, sort_keys=True),
                    max_concurrency,
                    json.dumps(normalized_labels, sort_keys=True),
                    protocol_version,
                    token_hash,
                    json.dumps(scope),
                    now,
                    now,
                ),
            )
        return f"{worker_id}.{secret}"

    def issue_register_token(self, *, workspace_id: str | None, label: str = "") -> tuple[str, str]:
        """Issue a scoped registration token; returns (token_id, plaintext).

        workspace_id=None mints a token that admits Workers to ALL workspaces.
        Only the sha256 hash is stored; the plaintext is returned exactly once.
        """
        if len(label) > _MAX_TOKEN_LABEL_LENGTH:
            raise ValueError(f"register token label exceeds {_MAX_TOKEN_LABEL_LENGTH} chars")
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        with write_transaction(self.database_dsn) as conn:
            if workspace_id is not None:
                exists = conn.execute(
                    "select 1 from workspaces where id=?", (workspace_id,)
                ).fetchone()
                if exists is None:
                    raise ValueError(f"workspace {workspace_id!r} does not exist")
            conn.execute(
                "insert into agent_register_tokens(id, token_hash, workspace_id, label)"
                " values (?, ?, ?, ?)",
                (token_id, token_hash, workspace_id, label),
            )
        return token_id, f"{token_id}.{secret}"

    def resolve_register_scope(self, token: str) -> list[str] | None:
        """Resolve a presented scoped register token to its workspace scope.

        Returns [] for an all-workspaces token, [workspace_id] for a scoped
        one, or None when the token is unknown or revoked."""
        token_id, separator, secret = token.partition(".")
        if not separator or not token_id or not secret:
            return None
        with read_connection(self.database_dsn) as conn:
            row = conn.execute(
                "select * from agent_register_tokens where id=?", (token_id,)
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not hmac.compare_digest(digest, row["token_hash"]):
            return None
        if row["workspace_id"] is None:
            return []
        return [str(row["workspace_id"])]

    def list_register_tokens(self) -> list[dict[str, Any]]:
        """List issued register tokens; never includes hash or plaintext."""
        with read_connection(self.database_dsn) as conn:
            rows = conn.execute(
                "select * from agent_register_tokens order by created_at, id"
            ).fetchall()
        return [
            {
                "token_id": row["id"],
                "workspace_id": row["workspace_id"],
                "label": row["label"],
                "created_at": row["created_at"],
                "revoked": row["revoked_at"] is not None,
            }
            for row in rows
        ]

    def revoke_register_token(self, token_id: str) -> bool:
        with write_transaction(self.database_dsn) as conn:
            result = conn.execute(
                "update agent_register_tokens set revoked_at=current_timestamp where id=?",
                (token_id,),
            )
            return result.rowcount > 0

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
        # Every Worker API call authenticates, and an idle Worker polls claim
        # every few seconds, so this is the liveness signal behind `online`.
        with write_transaction(self.database_dsn) as conn:
            conn.execute(
                "update agent_workers set last_seen_at=current_timestamp where worker_id=?",
                (worker_id,),
            )
        # Reflect the just-written timestamp so this call already reads online.
        return _worker_payload({**row, "last_seen_at": datetime.now(UTC)})

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
    last_seen_at = row["last_seen_at"]
    if isinstance(last_seen_at, str):
        last_seen_at = datetime.fromisoformat(last_seen_at)
    if last_seen_at is not None and last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    online = bool(
        last_seen_at is not None
        and datetime.now(UTC) - last_seen_at <= timedelta(seconds=_ONLINE_THRESHOLD_SECONDS)
    )
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "runtimes": json.loads(row["runtimes_json"]),
        "capabilities": json.loads(row["capabilities_json"] or "[]"),
        "models": json.loads(row["models_json"] or "[]"),
        "max_concurrency": int(row["max_concurrency"]),
        "labels": json.loads(row["labels_json"]),
        "protocol_version": int(row["protocol_version"]),
        "allowed_workspaces": json.loads(row["allowed_workspaces_json"] or "[]"),
        "registered_at": row["registered_at"],
        "last_seen_at": row["last_seen_at"],
        "online": online,
        "revoked": row["revoked_at"] is not None,
    }
