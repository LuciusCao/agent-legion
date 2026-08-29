from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class ScopedTokenManagementQueriesMixin(ConnectionQueriesMixin):
    """Self-service management queries for auth_scoped_tokens (schema v42).

    Backs /api/studio-agent-tokens: users list and revoke their own
    origin='user' tokens by public id. The token_hash digest never appears in
    these read paths.
    """

    def list_scoped_tokens(self, user_id: str, *, origin: str) -> list[dict[str, Any]]:
        """List one user's scoped tokens of a given origin (management view)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select id, created_at, expires_at, revoked_at
                from auth_scoped_tokens
                where user_id=%s and origin=%s
                order by created_at desc, id desc
                """,
                (user_id, origin),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_scoped_token_by_id(self, user_id: str, token_id: str, *, origin: str) -> bool:
        """Revoke one token by public id; False when not found for this user.

        Scoping the update by user_id (and origin) means revoking someone
        else's token is indistinguishable from an unknown id.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "update auth_scoped_tokens set revoked_at=current_timestamp"
                " where id=%s and user_id=%s and origin=%s and revoked_at is null",
                (token_id, user_id, origin),
            )
            return bool(cursor.rowcount)

    def delete_expired_scoped_tokens(self) -> int:
        """Batch-delete expired rows (revoked or not); lookups never match them.

        Called from the hourly maintenance sweep; without it expired rows
        accumulate forever and idx_auth_scoped_tokens_expires_at has no consumer.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "delete from auth_scoped_tokens where expires_at <= current_timestamp"
            )
            return cursor.rowcount
