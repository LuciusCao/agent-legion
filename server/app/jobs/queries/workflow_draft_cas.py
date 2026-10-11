"""Compare-and-set persistence for the Studio workflow YAML draft (#633).

Split from ``workflow_drafts.py`` (budget): the CAS upsert serves the agent
draft-save tool — an ``expected_updated_at`` that no longer matches the
stored row raises ``DraftConflictError`` instead of overwriting a newer
draft. #1226 五轮 P1: a row deleted by a publish leaves a tombstone
(``workflow_draft_tombstones``); a timestamp base at or before the deleted
row's ``updated_at`` belongs to the pre-publish generation (an in-flight
save that outlived the delete) and now conflicts instead of silently
recreating the row — a ``never-saved`` base still inserts freely (the
caller already knows there is no draft; its content is a new edit).
Constants (``DRAFT_NEVER_SAVED``) and the exception stay importable from
``workflow_drafts`` for the existing import surface.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.workflow_draft_tombstones import (
    clear_workflow_draft_tombstone,
    workflow_draft_base_is_stale,
)
from server.app.jobs.queries.workflow_drafts import (
    DRAFT_NEVER_SAVED,
    DraftConflictError,
)


class WorkflowDraftCasQueriesMixin:
    """CAS upsert for workspace_workflow_drafts (#633)."""

    def upsert_workspace_workflow_draft_if_unchanged(
        self,
        workspace_id: str,
        definition_yaml: str,
        expected_updated_at: str,
    ) -> dict[str, Any]:
        """Apply only when the stored draft's ``updated_at`` matches.

        The stored timestamp is compared AS a timestamptz against the parsed
        expected value — text renderings differ across channels (Postgres
        ``::text`` emits ``+00``, Python str() emits ``+00:00``), so a strict
        text match would reject the very value the caller just read. The
        ``where`` clause gates only the ON CONFLICT update branch: when no
        row exists at all, the plain INSERT would always succeed — so the
        timestamp branch first probes the publish tombstone (#1226 五轮 P1):
        a base covered by the tombstone (≤ the deleted row's updated_at) is
        a pre-publish generation write and conflicts (409) instead of
        resurrecting the deleted content; a ``never-saved`` base skips the
        probe and inserts freely. Every successful write clears the
        tombstone in the same transaction (it lives only while the row is
        absent). Unparseable timestamps never match — a malformed CAS token
        must not degrade into a blind overwrite.
        """
        with self.connect() as conn:  # type: ignore[attr-defined]
            if expected_updated_at == DRAFT_NEVER_SAVED:
                row = conn.execute(
                    """
                    insert into workspace_workflow_drafts(workspace_id, definition_yaml)
                    values (%s, %s)
                    on conflict(workspace_id) do update set
                      definition_yaml=excluded.definition_yaml,
                      updated_at=current_timestamp
                    where workspace_workflow_drafts.workspace_id is null
                    returning workspace_id, definition_yaml, created_at, updated_at
                    """,
                    (workspace_id, definition_yaml),
                ).fetchone()
            elif workflow_draft_base_is_stale(conn, workspace_id, expected_updated_at):  # type: ignore[attr-defined]
                # Pre-publish generation write (the row was deleted by a
                # publish while this save was in flight): conflict, do not
                # resurrect the deleted content.
                row = None
            else:
                row = conn.execute(
                    """
                    insert into workspace_workflow_drafts(workspace_id, definition_yaml)
                    values (%s, %s)
                    on conflict(workspace_id) do update set
                      definition_yaml=excluded.definition_yaml,
                      updated_at=current_timestamp
                    where workspace_workflow_drafts.updated_at = %s::timestamptz
                    returning workspace_id, definition_yaml, created_at, updated_at
                    """,
                    (workspace_id, definition_yaml, expected_updated_at),
                ).fetchone()
            if row is not None:
                clear_workflow_draft_tombstone(conn, workspace_id)
        if row is None:
            current = self.get_workspace_workflow_draft(workspace_id)  # type: ignore[attr-defined]
            raise DraftConflictError(
                "Workflow draft was modified by another session"
                f" (expected updated_at {expected_updated_at},"
                f" current updated_at"
                f" {current['updated_at'] if current else None})"
            )
        return dict(row)
