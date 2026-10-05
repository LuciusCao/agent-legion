"""Schema v90 (#924): ``studio_chat_sessions.archived_at`` — session archive.

Archive is the recoverable way to tidy the Studio chat session list (v89's
soft delete stays as the irreversible secondary action):

- the default list query filters ``archived_at is null``; the archive view
  lists only stamped rows;
- archiving closes any live runtime first-class (same close path, run token
  revoked), and the resume claim plus the spawn registration fence refuse a
  stamped row, so an archived session can only continue after unarchive;
- unarchive clears the stamp and never spawns a runtime: the row stays
  closed and the existing resume path brings it back.

A separate column rather than a new ``status`` value, for the same reason as
v89's ``deleted_at``: status is the live runtime state machine (active cap,
startup reaper, resume claim and the frontend all key on it) and archive is
an orthogonal visibility flag stamped on a closed row.

Same guarded-ALTER home rule as v87/v89: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay.
"""

from __future__ import annotations

from typing import Any

_ARCHIVE_DDL = """
alter table studio_chat_sessions
  add column if not exists archived_at timestamptz;
"""


def migrate_studio_chat_session_archive(conn: Any) -> None:
    """Add the nullable archived_at column (v90)."""
    conn.execute(_ARCHIVE_DDL)
