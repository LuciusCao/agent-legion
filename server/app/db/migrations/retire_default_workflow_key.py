"""Schema v91 (#211 M3): drop ``workspaces.default_workflow_key``.

The v62 binding made the column equal to the workspace id on every row
(creation writes id == key, PATCH / configuration reject any change, and
the v62 migration renamed legacy ids onto their keys), so it is pure
redundancy: every reader now uses the id. v70 already dropped the
workflow_key columns of the execution tables; this is the last live one.

Guarded and idempotent: fresh databases replay the terminal schema file
(no column), so the drop is a no-op there; a database at any older version
still carries the column through the earlier data migrations (v50 / v62 /
v64 read it) and loses it here, after the v62 binding has run.
"""

from __future__ import annotations

from typing import Any


def migrate_retire_default_workflow_key(conn: Any) -> None:
    """Drop the redundant workspace key column (v91)."""
    conn.execute("alter table workspaces drop column if exists default_workflow_key")
