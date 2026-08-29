"""Workspace-scoped Agent Worker registration token store (EXEC-WORKERACL-001).

Split from agent_workers.py for the file-size budget: everything about the
admin-issued registration credentials (issue / resolve / list / delete) lives
here. AgentWorkerRegistry inherits this store and adds the worker
registration lifecycle on top, so existing callers keep one entry point.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from typing import Any

from server.app.agent_control.register_token_deletion import delete_register_token_cascading
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction

_MAX_TOKEN_LABEL_LENGTH = 128


class AgentRegisterTokenStore:
    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self.database_dsn = database_dsn

    def issue_register_token(self, *, workspace_id: str, label: str = "") -> tuple[str, str]:
        """Issue a workspace-scoped registration token; returns (token_id, plaintext).

        workspace_id is required: the all-workspaces token variant was retired
        with the global register token (issue #35) — every registration must be
        attributable to exactly one workspace. Only the sha256 hash is stored;
        the plaintext is returned exactly once.
        """
        if not workspace_id:
            raise ValueError("workspace_id is required (all-workspaces tokens are retired)")
        if len(label) > _MAX_TOKEN_LABEL_LENGTH:
            raise ValueError(f"register token label exceeds {_MAX_TOKEN_LABEL_LENGTH} chars")
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        with write_transaction(self.database_dsn) as conn:
            exists = conn.execute(
                "select 1 from workspaces where id=%s", (workspace_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"workspace {workspace_id!r} does not exist")
            conn.execute(
                "insert into agent_register_tokens(id, token_hash, workspace_id, label)"
                " values (%s, %s, %s, %s)",
                (token_id, token_hash, workspace_id, label),
            )
        return token_id, f"{token_id}.{secret}"

    def resolve_register_scope(self, tokens: Sequence[str]) -> list[dict[str, Any]] | None:
        """Resolve presented scoped register tokens to their workspace scopes.

        Takes the worker's full token list; every token must resolve to a live
        (non-revoked) workspace-scoped token — one bad token fails the whole
        registration so a stale token can never silently narrow the scope.
        Returns the merged scope as [{'workspace_id', 'workspace_name',
        'token_ids'}] rows (deduplicated; token_ids records which presented
        tokens opened the workspace), or None when nothing resolved."""
        presented = [token for token in tokens if token]
        if not presented:
            return None
        scopes: dict[str, dict[str, Any]] = {}
        with read_connection(self.database_dsn) as conn:
            for token in presented:
                token_id, separator, secret = token.partition(".")
                if not separator or not token_id or not secret:
                    return None
                row = conn.execute(
                    "select t.token_hash, t.revoked_at, t.workspace_id, w.name"
                    " from agent_register_tokens t join workspaces w on w.id = t.workspace_id"
                    " where t.id=%s",
                    (token_id,),
                ).fetchone()
                if row is None or row["revoked_at"] is not None or row["workspace_id"] is None:
                    return None
                digest = hashlib.sha256(secret.encode()).hexdigest()
                if not hmac.compare_digest(digest, row["token_hash"]):
                    return None
                entry = scopes.setdefault(
                    str(row["workspace_id"]),
                    {"workspace_name": str(row["name"]), "token_ids": []},
                )
                entry["token_ids"].append(token_id)
        return [
            {"workspace_id": workspace_id, **entry}
            for workspace_id, entry in sorted(scopes.items())
        ]

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

    def delete_register_token(self, token_id: str) -> list[str] | None:
        """Hard-delete a register token (the only lifecycle action besides
        issue) and cascade-cut dependent Workers — see
        agent_register_token_deletion for the transaction. Returns the
        cascade-deleted worker_ids, or None when the token does not exist."""
        return delete_register_token_cascading(self.database_dsn, token_id)
