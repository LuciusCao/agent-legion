"""Persistence for studio chat sessions (schema v43, phase 3 chunk 4).

Message CRUD lives in studio_chat_messages.py (file budget split); this mixin
inherits it so the composed JobQueries surface is unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.studio_chat_messages import StudioChatMessageQueriesMixin

_SESSION_COLUMNS = (
    "id, workspace_id, user_id, agent_id, title, status, acp_session_id,"
    " capability_snapshot_json, allow_all_permissions, mcp_status, error_detail,"
    " created_at, updated_at, closed_at"
)


def _session_record(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["capability_snapshot"] = json.loads(record.pop("capability_snapshot_json") or "{}")
    return record


class StudioChatQueriesMixin(StudioChatMessageQueriesMixin):
    """CRUD for studio_chat_sessions (messages via the inherited mixin)."""

    def create_studio_chat_session(self, workspace_id: str, user_id: str, agent_id: str) -> str:
        session_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "insert into studio_chat_sessions(id, workspace_id, user_id, agent_id)"
                " values (%s, %s, %s, %s)",
                (session_id, workspace_id, user_id, agent_id),
            )
        return session_id

    def get_studio_chat_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_SESSION_COLUMNS} from studio_chat_sessions where id=%s",
                (session_id,),
            ).fetchone()
        return _session_record(row) if row is not None else None

    def list_studio_chat_sessions(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_SESSION_COLUMNS} from studio_chat_sessions"
                " where workspace_id=%s order by created_at desc, id desc",
                (workspace_id,),
            ).fetchall()
        return [_session_record(row) for row in rows]

    def update_studio_chat_session(self, session_id: str, **fields: Any) -> None:
        """Update whitelisted session columns; capability_snapshot is serialized here."""
        allowed = {
            "title",
            "status",
            "acp_session_id",
            "allow_all_permissions",
            "mcp_status",
            "error_detail",
            "closed_at",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "capability_snapshot":
                updates["capability_snapshot_json"] = json.dumps(value)
            elif key in allowed:
                updates[key] = value
            else:
                raise ValueError(f"unsupported studio chat session field: {key}")
        if not updates:
            return
        assignments = ", ".join(f"{key}=%s" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"update studio_chat_sessions set {assignments},"
                " updated_at=current_timestamp where id=%s",
                (*updates.values(), session_id),
            )

    def claim_studio_chat_turn(self, session_id: str) -> bool:
        """Atomically move a session idle -> running; False when not idle.

        The check-and-set happens in one UPDATE so two concurrent senders
        cannot both observe "idle" and start duplicate turns.
        """
        with self.connect() as conn:
            row = conn.execute(
                "update studio_chat_sessions set status='running',"
                " updated_at=current_timestamp where id=%s and status='idle' returning id",
                (session_id,),
            ).fetchone()
        return row is not None
