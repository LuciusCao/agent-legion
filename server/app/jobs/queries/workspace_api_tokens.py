from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class WorkspaceApiTokenQueriesMixin(ConnectionQueriesMixin):
    """Persistence for workspace_api_tokens (schema v83, #626).

    Raw row access for the machine-to-machine intake credentials. The auth
    semantics — sha256 hashing, hmac digest comparison, TTL / revoke
    interpretation, the last_used_at refresh throttle — live in
    ``server.app.auth.workspace_api_tokens`` on top of these methods, so no
    digest or plaintext ever crosses this boundary (the callers hand over
    pre-hashed values and receive projection rows).
    """

    def create_workspace_api_token(
        self,
        token_id: str,
        token_hash: str,
        workspace_id: str,
        label: str,
        expires_at: datetime | None,
    ) -> None:
        """Insert one token row; raises ValueError for an unknown workspace.

        The existence check and the INSERT share one transaction so a
        workspace deleted concurrently cannot leave an orphaned token.
        """
        with self.connect() as conn:
            exists = conn.execute(
                "select 1 from workspaces where id=%s", (workspace_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"workspace {workspace_id!r} does not exist")
            conn.execute(
                "insert into workspace_api_tokens(id, token_hash, workspace_id, label,"
                " expires_at) values (%s, %s, %s, %s, %s)",
                (token_id, token_hash, workspace_id, label, expires_at),
            )

    def get_workspace_api_token_row(self, token_id: str) -> dict[str, Any] | None:
        """Fetch the auth-relevant columns for one token id, or None.

        The auth hot path (#626): a single indexed SELECT feeding the digest
        comparison and the expiry / revoke checks in the store. The row
        carries token_hash — for the store's hmac comparison only, never for
        API output.
        """
        with self._connect_read() as conn:
            row = conn.execute(
                "select token_hash, workspace_id, revoked_at, expires_at"
                " from workspace_api_tokens where id=%s",
                (token_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_workspace_api_token_last_used(self, token_id: str) -> None:
        """Stamp last_used_at; revoked rows are deliberately left untouched."""
        with self.connect() as conn:
            conn.execute(
                "update workspace_api_tokens set last_used_at=current_timestamp"
                " where id=%s and revoked_at is null",
                (token_id,),
            )

    def list_workspace_api_tokens(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """List tokens (one workspace's, or all); never hash or plaintext."""
        with self._connect_read() as conn:
            if workspace_id is None:
                rows = conn.execute(
                    "select * from workspace_api_tokens order by created_at, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "select * from workspace_api_tokens where workspace_id=%s"
                    " order by created_at, id",
                    (workspace_id,),
                ).fetchall()
        return [
            {
                "token_id": row["id"],
                "workspace_id": str(row["workspace_id"]),
                "label": row["label"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "revoked": row["revoked_at"] is not None,
                "last_used_at": row["last_used_at"],
            }
            for row in rows
        ]

    def revoke_workspace_api_token(self, token_id: str, workspace_id: str | None = None) -> bool:
        """Soft-revoke one token; False when it does not exist (or belongs to
        another workspace when workspace_id is given — same False, no leak)."""
        with self.connect() as conn:
            if workspace_id is None:
                cursor = conn.execute(
                    "update workspace_api_tokens set revoked_at=current_timestamp"
                    " where id=%s and revoked_at is null",
                    (token_id,),
                )
            else:
                cursor = conn.execute(
                    "update workspace_api_tokens set revoked_at=current_timestamp"
                    " where id=%s and workspace_id=%s and revoked_at is null",
                    (token_id, workspace_id),
                )
            return bool(cursor.rowcount)
