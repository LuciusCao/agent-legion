"""Schema v89 (#872): ``studio_chat_sessions.deleted_at`` — session soft delete.

The Studio chat session list only ever grew: the DELETE endpoint means
*close* (status='closed', the session stays listed and resumable). Deleting a
session from the list stamps ``deleted_at`` instead of dropping the row:

- the list query filters ``deleted_at is null``;
- the service's public read (``StudioChatService.get_session``) answers 404
  for a stamped row, so every session-scoped endpoint (messages, SSE,
  resume, context, ...) treats it as gone;
- the resume claim refuses stamped rows (no resurrection race).

A separate column rather than a new ``status`` value: status is the live
runtime state machine (closed/error rows stay resumable, the active-session
cap and the startup reaper key on it, the frontend renders it), while
deletion is an orthogonal visibility flag stamped on an already-closed row.
Message rows are kept (``on delete cascade`` only fires on a hard delete).

Same guarded-ALTER home rule as v78/v80/v81/v87: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_SOFT_DELETE_DDL = """
alter table studio_chat_sessions
  add column if not exists deleted_at timestamptz;
"""


def migrate_studio_chat_session_soft_delete(conn: Any) -> None:
    """Add the nullable deleted_at column (v89)."""
    conn.execute(_SOFT_DELETE_DDL)
