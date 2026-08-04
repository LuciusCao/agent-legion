from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from server.app.auth.sessions import session_expiry
from server.app.jobs.queries.base import JobQueriesBase

USER_ROLES = ("admin", "member")
WORKSPACE_MEMBER_ROLES = ("editor", "viewer")


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record.pop("password_hash", None)
    return record


class AuthQueriesMixin(JobQueriesBase):
    """Persistence for users, sessions, and workspace membership."""

    # --- users -----------------------------------------------------------

    def count_users(self) -> int:
        with self._connect_read() as conn:
            row = conn.execute("select count(*) as n from users").fetchone()
        return int(row["n"]) if row is not None else 0

    def create_user(
        self,
        username: str,
        *,
        display_name: str = "",
        password_hash: str | None = None,
        role: str = "member",
    ) -> dict[str, Any]:
        clean_username = username.strip()
        if not clean_username:
            raise ValueError("Username is required")
        if role not in USER_ROLES:
            raise ValueError(f"Unknown user role: {role}")
        user_id = uuid.uuid4().hex
        with self.connect() as conn:
            exists = conn.execute(
                "select 1 from users where username=?", (clean_username,)
            ).fetchone()
            if exists is not None:
                raise ValueError("Username already exists")
            conn.execute(
                """
                insert into users(id, username, display_name, password_hash, role)
                values (?, ?, ?, ?, ?)
                """,
                (user_id, clean_username, display_name.strip(), password_hash, role),
            )
            row = conn.execute("select * from users where id=?", (user_id,)).fetchone()
        if row is None:
            raise RuntimeError("user insert did not return a row")
        return _public_user(row)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from users where id=?", (user_id,)).fetchone()
        return _public_user(row) if row else None

    def get_user_credentials(self, username: str) -> dict[str, Any] | None:
        """Internal record including password_hash; auth service use only."""
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from users where username=?", (username.strip(),)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from users order by created_at, id")
            return [_public_user(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
        disabled: bool | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if display_name is not None:
            fields["display_name"] = display_name.strip()
        if role is not None:
            if role not in USER_ROLES:
                raise ValueError(f"Unknown user role: {role}")
            fields["role"] = role
        if password_hash is not None:
            fields["password_hash"] = password_hash
        if disabled is not None:
            fields["disabled_at"] = datetime.now(UTC) if disabled else None
        with self.connect() as conn:
            if fields:
                assignments = ", ".join(f"{key}=?" for key in fields)
                params = list(fields.values()) + [user_id]
                cursor = conn.execute(
                    f"update users set {assignments}, updated_at=current_timestamp where id=?",
                    params,
                )
                if cursor.rowcount == 0:
                    raise ValueError("User not found")
                if "disabled_at" in fields or "password_hash" in fields:
                    conn.execute(
                        "update sessions set revoked_at=current_timestamp"
                        " where user_id=? and revoked_at is null",
                        (user_id,),
                    )
            row = conn.execute("select * from users where id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found")
        return _public_user(row)

    # --- sessions --------------------------------------------------------

    def create_session(self, token_hash: str, user_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "insert into sessions(token_hash, user_id, expires_at) values (?, ?, ?)",
                (token_hash, user_id, session_expiry()),
            )

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None:
        """Resolve a session token digest to its user, sliding the expiry.

        Returns None for unknown, revoked, or expired sessions, and for users
        that have been disabled since the session was issued.
        """
        with self.connect() as conn:
            row = conn.execute(
                """
                select u.* from sessions s
                join users u on u.id = s.user_id
                where s.token_hash=? and s.revoked_at is null
                  and s.expires_at > current_timestamp
                  and u.disabled_at is null
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update sessions set expires_at=? where token_hash=?",
                (session_expiry(), token_hash),
            )
        return _public_user(row)

    def revoke_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update sessions set revoked_at=current_timestamp"
                " where token_hash=? and revoked_at is null",
                (token_hash,),
            )

    # --- workspace membership --------------------------------------------

    def get_workspace_role(self, workspace_id: str, user_id: str) -> str | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select role from workspace_members where workspace_id=? and user_id=?",
                (workspace_id, user_id),
            ).fetchone()
        return str(row["role"]) if row else None

    def list_workspace_members(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select u.id, u.username, u.display_name, u.role as user_role,
                       u.disabled_at, m.role as member_role, m.created_at as member_since
                from workspace_members m
                join users u on u.id = m.user_id
                where m.workspace_id=?
                order by m.created_at, u.id
                """,
                (workspace_id,),
            )
            return [dict(row) for row in rows]

    def list_user_workspace_ids(self, user_id: str) -> list[str]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select workspace_id from workspace_members where user_id=?",
                (user_id,),
            )
            return [str(row["workspace_id"]) for row in rows]

    def upsert_workspace_member(self, workspace_id: str, user_id: str, role: str) -> None:
        if role not in WORKSPACE_MEMBER_ROLES:
            raise ValueError(f"Unknown workspace member role: {role}")
        with self.connect() as conn:
            for table, value in (("workspaces", workspace_id), ("users", user_id)):
                exists = conn.execute(f"select 1 from {table} where id=?", (value,)).fetchone()
                if exists is None:
                    raise ValueError(f"{table[:-1].capitalize()} not found")
            conn.execute(
                """
                insert into workspace_members(workspace_id, user_id, role)
                values (?, ?, ?)
                on conflict(workspace_id, user_id) do update set role=excluded.role
                """,
                (workspace_id, user_id, role),
            )

    def delete_workspace_member(self, workspace_id: str, user_id: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                "delete from workspace_members where workspace_id=? and user_id=?",
                (workspace_id, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace member not found")
