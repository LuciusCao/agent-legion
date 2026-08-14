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
    ) -> str:
        """Insert a scoped token row and return its public (non-digest) id."""
        token_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "insert into auth_scoped_tokens(id, token_hash, user_id, scope, origin,"
                " expires_at) values (%s, %s, %s, %s, %s, %s)",
                (token_id, token_hash, user_id, scope, origin, expires_at),
            )
        return token_id

    def get_scoped_token_user(self, token_hash: str) -> dict[str, Any] | None:
        """Resolve a scoped token digest to its user row plus scope, or None.

        Fixed TTL: unlike sessions there is no sliding expiry. Returns None for
        unknown, revoked, or expired tokens and for users disabled since the
        token was minted.
        """
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select u.*, t.scope from auth_scoped_tokens t
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
