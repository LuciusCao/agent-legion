"""Schema v95 (#1226 五轮 codex P1): publish-generation tombstones for the
Studio workflow draft.

#1221 made a successful publish delete the ``workspace_workflow_drafts`` row
in the revision transaction. One writer class still escaped: a draft save
that had ALREADY passed its debounce and was waiting on the draft row lock
(in-flight PUT, another tab's autosave) re-INSERTed the pre-publish content
after the delete committed — the CAS ``where`` gates only the update branch,
so an absent row meant a blind insert.

The tombstone closes it: the publish's delete records the deleted row's
``updated_at`` here (same transaction), and the CAS save's timestamp branch
first takes the draft row's FOR UPDATE lock (waiting out any in-flight
publish) and then probes this table on a FRESH post-wait snapshot — a base
timestamp at or before ``deleted_updated_at`` belongs to the pre-publish
generation and is refused with a conflict (409) instead of resurrecting the
row. ``never-saved`` bases skip the probe: the caller already knows there is
no draft, so its content is a genuinely new edit. The plain last-write-wins
upsert carries no base and cannot tell the generations apart — it stays
unconditional (legacy clients only; every current writer sends a CAS base).

Lifecycle: a tombstone row lives only while the draft row is absent. Every
successful draft write clears it in the same transaction (row exists ⇒ no
tombstone), so the probe only ever runs in the row-absent window and never
accumulates; workspace deletion cascades.

This module owns the table's DDL — postgres_schema.sql does not create it
(the schema file sits at its absolute line ceiling; fresh and pre-v95
databases both run this apply fn — the parity test pins the shapes equal).
"""

from __future__ import annotations

from typing import Any

_WORKFLOW_DRAFT_TOMBSTONES_DDL = """
create table if not exists workspace_workflow_draft_tombstones (
  workspace_id text primary key references workspaces(id) on delete cascade,
  deleted_updated_at timestamptz not null,
  deleted_at timestamptz not null default current_timestamp
)
"""


def migrate_workflow_draft_tombstones(conn: Any) -> None:
    """Create the draft tombstone table (v95); idempotent on replay."""
    conn.execute(_WORKFLOW_DRAFT_TOMBSTONES_DDL)
