"""Atomic human-turn admission: authentication, idle claim and user message.

No turn claim survives a failed INSERT or commit. Token/user locks serialize
admission against revocation and disabling; expiry is checked at admission.
The caller holds the runtime and handle locks until the committed input is
queued, so close/stop cannot accept a durable message then refuse its queue.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class StudioChatAdmissionRejected(Exception):
    """No durable input was accepted (token invalid or session not idle)."""


def _lock_live_token(conn: Any, token_hash: str) -> None:
    token = conn.execute(
        "select t.id from auth_scoped_tokens t join users u on u.id=t.user_id"
        " where t.token_hash=%s and t.revoked_at is null"
        " and t.expires_at > clock_timestamp() and u.disabled_at is null"
        " for share of t, u",
        (token_hash,),
    ).fetchone()
    if token is None:
        raise StudioChatAdmissionRejected


def _user_record(
    message_id: str, session_id: str, content: dict[str, Any], row: Any
) -> dict[str, Any]:
    return {
        "id": message_id,
        "session_id": session_id,
        "kind": "text",
        "role": "user",
        "content": content,
        "seq": row["seq"],
        "created_at": row["created_at"],
    }


class StudioChatAdmissionQueriesMixin(ConnectionQueriesMixin):
    def accept_studio_chat_message(
        self, session_id: str, token_hash: str, text: str
    ) -> dict[str, Any]:
        message_id = uuid4().hex
        content = {"text": text}
        with self.connect() as conn:
            _lock_live_token(conn, token_hash)
            claimed = conn.execute(
                "update studio_chat_sessions set status='running', updated_at=current_timestamp"
                " where id=%s and status='idle' returning id",
                (session_id,),
            ).fetchone()
            if claimed is None:
                raise StudioChatAdmissionRejected
            row = conn.execute(
                "insert into studio_chat_messages(id,session_id,kind,role,content_json)"
                " values (%s,%s,'text','user',%s) returning seq,created_at",
                (message_id, session_id, json.dumps(content)),
            ).fetchone()
        assert row is not None
        return _user_record(message_id, session_id, content, row)

    def enqueue_studio_chat_message(
        self, session_id: str, token_hash: str, text: str
    ) -> dict[str, Any]:
        """#882 inbound queue: persist a human message that waits behind the
        turn in flight (``content.queued``) without claiming the session.
        Same token gate as admission; the session must still be live."""
        message_id = uuid4().hex
        content = {"text": text, "queued": True}
        with self.connect() as conn:
            _lock_live_token(conn, token_hash)
            row = conn.execute(
                "with live as (select id from studio_chat_sessions where id=%s"
                " and status in ('idle','running','awaiting_permission')"
                " and deleted_at is null for share)"
                " insert into studio_chat_messages(id,session_id,kind,role,content_json)"
                " select %s, live.id, 'text', 'user', %s from live returning seq,created_at",
                (session_id, message_id, json.dumps(content)),
            ).fetchone()
            if row is None:
                raise StudioChatAdmissionRejected
        return _user_record(message_id, session_id, content, row)
