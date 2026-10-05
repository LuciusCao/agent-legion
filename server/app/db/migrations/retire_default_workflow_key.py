"""Schema v91 (#211 M3): drop the last live workflow-key columns.

``workspaces.default_workflow_key`` and ``quality_sample_batches.workflow_key``.

The v62 binding made the column equal to the workspace id on every row
(creation writes id == key, PATCH / configuration reject any change, and
the v62 migration renamed legacy ids onto their keys), so it is pure
redundancy: every reader now uses the id. v70 already dropped the
workflow_key columns of the execution tables. The quality sample batch
column only ever mirrored the batch's workspace id (sampling binds
jobs.workspace_id since #211 Phase 3) and is dropped in the same step so
no writable second workflow identifier survives (codex R2 on #1032).

Guarded and idempotent: fresh databases replay the terminal schema file
(no column), so the drop is a no-op there; a database at any older version
still carries the column through the earlier data migrations (v50 / v62 /
v64 read it) and loses it here, after the v62 binding has run.
"""

from __future__ import annotations

from typing import Any


def migrate_retire_default_workflow_key(conn: Any) -> None:
    """Drop the redundant workflow-key columns (v91)."""
    conn.execute("alter table workspaces drop column if exists default_workflow_key")
    conn.execute("alter table quality_sample_batches drop column if exists workflow_key")
