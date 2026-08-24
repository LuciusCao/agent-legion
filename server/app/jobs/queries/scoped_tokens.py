from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.base import JobQueriesBase


class ScopedTokenQueriesMixin(JobQueriesBase):
    """Persistence for auth_scoped_tokens (schema v41/v42, STUDIO-AGENT-001)."""

    def create_scoped_token(
        self,
        token_hash: str,
        user_id: str,
        scope: str,
        expires_at: datetime,
        *,
        origin: str = "run",
        workspace_id: str | None = None,
    ) -> str:
        """Insert a scoped token row and return its public (non-digest) id."""
        # str(uuid4()) matches the DB default gen_random_uuid()::text format.
        # workspace_id rides the same INSERT: the run token's workspace binding
        # is born atomically with the row, never in a follow-up UPDATE.
        token_id = str(uuid4())
        with self.connect() as conn:
            conn.execute(
                "insert into auth_scoped_tokens(id, token_hash, user_id, scope, origin,"
                " workspace_id, expires_at) values (%s, %s, %s, %s, %s, %s, %s)",
                (token_id, token_hash, user_id, scope, origin, workspace_id, expires_at),
            )
        return token_id

    def get_scoped_token_user(self, token_hash: str) -> dict[str, Any] | None:
        """Resolve a scoped token digest to its user row plus scope, or None.

        Returns None for unknown, revoked, or expired tokens and for users
        disabled since the token was minted.
        """
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select u.*, t.scope, t.workspace_id as scoped_workspace_id from auth_scoped_tokens t
                join users u on u.id = t.user_id
                where t.token_hash=%s and t.revoked_at is null
                  and t.expires_at > current_timestamp
                  and u.disabled_at is null
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record.pop("password_hash", None)
        return record

    def revoke_scoped_token(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update auth_scoped_tokens set revoked_at=current_timestamp"
                " where token_hash=%s and revoked_at is null",
                (token_hash,),
            )

    def extend_scoped_token_expiry(
        self, token_hash: str, new_expires_at: datetime, if_expiring_before: datetime
    ) -> None:
        """Slide a live token's expiry forward, only when it is close to expiry.

        Single conditional UPDATE: a revoked token stays revoked, a token with
        more life than ``if_expiring_before`` is left untouched, and an
        already-expired token is NOT revived — a leaked token that died while
        the session sat idle must stay dead (#158 review).
        """
        with self.connect() as conn:
            conn.execute(
                "update auth_scoped_tokens set expires_at=%s"
                " where token_hash=%s and revoked_at is null"
                " and expires_at > current_timestamp and expires_at < %s",
                (new_expires_at, token_hash, if_expiring_before),
            )
