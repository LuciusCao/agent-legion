"""Schema v95 (#1226 五轮收口, 设计 v2): the Studio draft generation counter.

``workspace_workflow_draft_generation`` is the workspace-level draft
generation: every draft write and every publish's row delete advances it by
one (upsert, single statement). The draft CAS save declares the generation
it bases on; a stale base conflicts (409) instead of overwriting a newer
draft or resurrecting one a publish deleted (#1226 五轮 P1), and the
never-saved special case disappears (an empty read's generation is still a
generation — R6).

Born on this branch as the tombstone table (记被删行 updated_at 的近似),
rewritten in place to the generation model before merge (unmerged branches
may rewrite their migration; #434 collision protocol numbers it past
origin/main's v94).

This module owns the table's DDL — postgres_schema.sql does not create it
(the schema file sits at its absolute line ceiling; fresh and pre-v95
databases both run this apply fn — the parity test pins the shapes equal).
"""

from __future__ import annotations

from typing import Any

_WORKFLOW_DRAFT_GENERATION_DDL = """
create table if not exists workspace_workflow_draft_generation (
  workspace_id text primary key references workspaces(id) on delete cascade,
  generation bigint not null
)
"""


def migrate_workflow_draft_generation(conn: Any) -> None:
    """Create the draft generation table (v95); idempotent on replay."""
    conn.execute(_WORKFLOW_DRAFT_GENERATION_DDL)
