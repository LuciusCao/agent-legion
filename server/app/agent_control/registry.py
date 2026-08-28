from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_control.declarations import (
    normalize_labels,
    normalize_worker_declarations,
)
from server.app.agent_control.liveness import WorkerLiveness
from server.app.agent_control.register_key_guard import resolve_issue_scope
from server.app.agent_control.register_tokens import AgentRegisterTokenStore
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from shared.protocol import CODE_PROTOCOL_VERSION, MODEL_RUNTIME_PROTOCOL_VERSION

# Worker-supplied registration fields are scheduling inputs from a
# management-token holder, not trusted identity: keep them bounded so a
# malformed or hostile registration cannot bloat rows or smuggle control
# characters into logs/UI. worker_id additionally joins the token format
# "<worker_id>.<secret>", so '.' must stay excluded.
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_NAME_LENGTH = 128
_MAX_CONCURRENCY = 1024
# A Worker is "online" while its last authenticated call (claim poll every few
# seconds, or an execution heartbeat) is fresher than this threshold.
ONLINE_THRESHOLD_SECONDS = 30
# Protocol constants moved to shared/protocol.py (the worker image ships
# shared/, so both sides import one copy); re-exported here for the existing
# server-side consumers.


class AgentWorkerRegistry(AgentRegisterTokenStore):
    """Worker registration lifecycle; the admin-issued register token store
    (issue/resolve/list/delete) is inherited from AgentRegisterTokenStore."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        super().__init__(database_dsn)
        self._liveness = WorkerLiveness()

    def delete_register_token(self, token_id: str) -> list[str] | None:
        deleted = super().delete_register_token(token_id)
        # Evict cascade-deleted Workers from the liveness memo so the throttle
        # dict stays bounded.
        for worker_id in deleted or []:
            self._liveness.discard(worker_id)
        return deleted

    def issue_token(
        self,
        *,
        worker_id: str,
        name: str,
        runtimes: list[str],
        capabilities: Sequence[str] | None = None,
        models: Sequence[Mapping[str, Any]] | None = None,
        max_concurrency: int,
        max_code_concurrency: int = 0,
        labels: Mapping[str, Any] | None = None,
        protocol_version: int = 1,
        image_version: str = "",
        allowed_workspaces: Sequence[str] | None = None,
        register_token_ids: Sequence[str] | None = None,
    ) -> str:
        # image_version is accepted for forward compatibility but not stored:
        # the agent_workers table has no column for it yet.
        if not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker_id must be 1-64 chars of [A-Za-z0-9_-] starting alphanumeric")
        if len(name) > _MAX_NAME_LENGTH:
            raise ValueError(f"worker name exceeds {_MAX_NAME_LENGTH} chars")
        if not 0 < max_concurrency <= _MAX_CONCURRENCY:
            raise ValueError(f"max_concurrency must be between 1 and {_MAX_CONCURRENCY}")
        if not 0 <= max_code_concurrency <= _MAX_CONCURRENCY:
            raise ValueError(f"max_code_concurrency must be between 0 and {_MAX_CONCURRENCY}")
        if max_code_concurrency > 0 and protocol_version < CODE_PROTOCOL_VERSION:
            raise ValueError(f"code capacity requires protocol_version >= {CODE_PROTOCOL_VERSION}")
        normalized_runtimes = sorted(set(runtimes))
        if not normalized_runtimes or any(
            runtime not in {"pi", "openclaw", "velites"} for runtime in normalized_runtimes
        ):
            raise ValueError("runtimes must contain pi, openclaw and/or velites")
        normalized_labels = normalize_labels(labels or {})
        # None is kept as an internal compatibility mode for older direct
        # registry callers; the HTTP contract always supplies explicit lists.
        normalized_capabilities, normalized_models = normalize_worker_declarations(
            capabilities,
            models,
            normalized_runtimes,
            require_model_runtime=protocol_version >= MODEL_RUNTIME_PROTOCOL_VERSION,
        )
        # The workspace scope is ALWAYS resolved server-side from the presented
        # registration credentials (route layer), never from Worker fields:
        # each scoped register token contributes its workspace id; the merged
        # list is stored. Re-registering rotates the token AND refreshes the
        # scope from the current credentials. With bound keys the scope is
        # re-derived from the locked key rows below; allowed_workspaces only
        # serves legacy direct callers with no key binding.
        token_ids = sorted({str(token_id) for token_id in (register_token_ids or [])})
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        now = datetime.now(UTC)
        with write_transaction(self.database_dsn) as conn:
            # Revalidation happens inside THIS transaction: a key deleted
            # after the route's read-only resolve must abort the registration,
            # or the delete-key-cuts-access guarantee is lost (the worker row
            # is invisible to the cascade until commit).
            scope = resolve_issue_scope(conn, token_ids, allowed_workspaces)
            conn.execute(
                """
                insert into agent_workers(
                  worker_id, name, runtimes_json, capabilities_json, models_json,
                  max_concurrency, max_code_concurrency, labels_json,
                  protocol_version, token_hash, allowed_workspaces_json,
                  register_token_ids_json,
                  registered_at, last_seen_at, revoked_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, null)
                on conflict(worker_id) do update set
                  name=excluded.name,
                  runtimes_json=excluded.runtimes_json,
                  capabilities_json=excluded.capabilities_json,
                  models_json=excluded.models_json,
                  max_concurrency=excluded.max_concurrency,
                  max_code_concurrency=excluded.max_code_concurrency,
                  labels_json=excluded.labels_json,
                  protocol_version=excluded.protocol_version,
                  token_hash=excluded.token_hash,
                  allowed_workspaces_json=excluded.allowed_workspaces_json,
                  register_token_ids_json=excluded.register_token_ids_json,
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
                    max_code_concurrency,
                    json.dumps(normalized_labels, sort_keys=True),
                    protocol_version,
                    token_hash,
                    json.dumps(scope),
                    json.dumps(token_ids),
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
                "select * from agent_workers where worker_id=%s", (worker_id,)
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not hmac.compare_digest(digest, row["token_hash"]):
            return None
        # Every Worker API call authenticates, and an idle Worker polls claim
        # every few seconds, so this is the liveness signal behind `online`.
        # The write is throttled (WorkerLiveness): at agent scale a write
        # transaction per call is the hottest write path on the Host.
        self._liveness.record_seen(self.database_dsn, worker_id)
        # Reflect a fresh timestamp so this call already reads online, even
        # when the throttled write was skipped.
        return _worker_payload({**row, "last_seen_at": datetime.now(UTC)})

    def list_workers(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """List registered workers, optionally narrowed to one workspace.

        A workspace view only ever sees workers whose stored scope contains
        that workspace (i.e. registered with one of its scoped tokens); the
        [] legacy scope (global-token registrations) is excluded on purpose —
        those workers are visible to admins only until they re-register."""
        with read_connection(self.database_dsn) as conn:
            if workspace_id is None:
                rows = conn.execute("select * from agent_workers order by worker_id").fetchall()
            else:
                rows = conn.execute(
                    "select * from agent_workers"
                    " where allowed_workspaces_json::jsonb @> jsonb_build_array(%s::text)"
                    " order by worker_id",
                    (workspace_id,),
                ).fetchall()
        return [_worker_payload(row) for row in rows]

    def delete_worker(self, worker_id: str) -> str:
        """Hard-delete a worker registration; blocked while a bound key lives.

        There is no per-worker revocation by design: a Worker is just a client
        of its register keys, so access is cut by deleting the key (the Worker
        then fails its next re-registration). Deleting the record is the
        follow-up cleanup step and only opens once none of the Worker's bound
        keys exist anymore (legacy pre-v59 registrations have no recorded
        binding and are always deletable — they are the migration cleanup
        target). The check and the delete happen in one transaction so a
        registration record never vanishes under in-flight claims. Returns
        'deleted', 'not_found', or 'keys_active'. Historical execution rows
        reference worker_id as plain text (no FK), so removing the row does
        not touch them."""
        with write_transaction(self.database_dsn) as conn:
            row = conn.execute(
                "select register_token_ids_json from agent_workers where worker_id=%s for update",
                (worker_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            bound = json.loads(row["register_token_ids_json"] or "[]")
            if bound:
                alive = conn.execute(
                    # revoked_at rows (legacy v58 revoke leftovers) do not
                    # block the manual cleanup delete: they can no longer
                    # admit registrations, so they are not "alive" keys.
                    "select 1 from agent_register_tokens"
                    " where id = any(%s) and revoked_at is null limit 1",
                    (bound,),
                ).fetchone()
                if alive is not None:
                    return "keys_active"
            conn.execute(
                "delete from agent_workers where worker_id=%s",
                (worker_id,),
            )
        self._liveness.discard(worker_id)
        return "deleted"


def _worker_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    last_seen_at = row["last_seen_at"]
    if isinstance(last_seen_at, str):
        last_seen_at = datetime.fromisoformat(last_seen_at)
    if last_seen_at is not None and last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    online = bool(
        last_seen_at is not None
        and datetime.now(UTC) - last_seen_at <= timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    )
    registered_at = row["registered_at"]
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "runtimes": json.loads(row["runtimes_json"]),
        "capabilities": json.loads(row["capabilities_json"] or "[]"),
        "models": json.loads(row["models_json"] or "[]"),
        "max_concurrency": int(row["max_concurrency"]),
        "max_code_concurrency": int(row["max_code_concurrency"]),
        "labels": json.loads(row["labels_json"]),
        "protocol_version": int(row["protocol_version"]),
        "allowed_workspaces": json.loads(row["allowed_workspaces_json"] or "[]"),
        # Absent only for pre-v59 rows in test doubles; real rows always have
        # the column (default '[]').
        "register_token_ids": json.loads(row.get("register_token_ids_json") or "[]"),
        "registered_at": (
            registered_at.isoformat() if isinstance(registered_at, datetime) else str(registered_at)
        ),
        "last_seen_at": (
            last_seen_at.isoformat() if isinstance(last_seen_at, datetime) else str(last_seen_at)
        ),
        "online": online,
        "revoked": row["revoked_at"] is not None,
    }
