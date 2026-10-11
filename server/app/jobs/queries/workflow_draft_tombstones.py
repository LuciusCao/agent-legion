"""Publish-generation tombstones for the Studio workflow draft (#1226 五轮 P1).

The publish delete hook records the deleted draft row's ``updated_at`` here
(same transaction); the CAS save's timestamp branch probes it before
re-INSERTing an absent row: a base at or before ``deleted_updated_at``
belongs to the pre-publish generation (an in-flight save that was waiting
on the draft row lock while the publish committed) and must conflict
instead of resurrecting the deleted content. A tombstone lives only while
the draft row is absent — every successful draft write clears it in the
same transaction, so the probe never runs on the hot path and the table
never accumulates.

Module-level helpers (not mixin methods): only sibling queries modules call
them, always on a caller-owned transaction connection — same idiom as
``workflow_revision_projection``.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection

#: The tombstone probe of the CAS timestamp branch: base covered by the
#: publish that deleted the row ⇒ the writer's content is pre-publish.
_TOMBSTONE_COVER_SQL = """
select 1 from workspace_workflow_draft_tombstones
where workspace_id=%s and deleted_updated_at >= %s::timestamptz
"""


def workflow_draft_base_is_stale(
    conn: DatabaseConnection, workspace_id: str, expected_updated_at: str
) -> bool:
    """Whether a publish tombstone covers this CAS base timestamp.

    Two steps, order matters: the FOR UPDATE on the draft row first waits
    out any in-flight publish holding the row lock (its delete + tombstone
    commit together); the probe then runs on a fresh post-wait READ
    COMMITTED snapshot, so a just-committed tombstone is always visible —
    the candidate-row snapshot of a single INSERT...WHERE NOT EXISTS would
    predate the wait and miss it.
    """
    conn.execute(
        "select 1 from workspace_workflow_drafts where workspace_id=%s for update",
        (workspace_id,),
    )
    return (
        conn.execute(_TOMBSTONE_COVER_SQL, (workspace_id, expected_updated_at)).fetchone()
        is not None
    )


def record_workflow_draft_tombstone(
    conn: DatabaseConnection, workspace_id: str, deleted_updated_at: Any
) -> None:
    """Record (or refresh) the publish tombstone for one workspace."""
    conn.execute(
        """
        insert into workspace_workflow_draft_tombstones(workspace_id, deleted_updated_at)
        values (%s, %s)
        on conflict(workspace_id) do update set
          deleted_updated_at=excluded.deleted_updated_at,
          deleted_at=current_timestamp
        """,
        (workspace_id, deleted_updated_at),
    )


def workflow_draft_tombstone_covers(
    conn: DatabaseConnection, workspace_id: str, expected_updated_at: str
) -> bool:
    """Whether a publish tombstone covers this CAS base timestamp."""
    return (
        conn.execute(_TOMBSTONE_COVER_SQL, (workspace_id, expected_updated_at)).fetchone()
        is not None
    )


def clear_workflow_draft_tombstone(conn: DatabaseConnection, workspace_id: str) -> None:
    """Clear the tombstone once a draft row exists again (lifecycle rule)."""
    conn.execute(
        "delete from workspace_workflow_draft_tombstones where workspace_id=%s",
        (workspace_id,),
    )
