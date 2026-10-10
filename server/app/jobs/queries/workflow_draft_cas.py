"""Compare-and-set persistence for the Studio workflow YAML draft (#633).

Split from ``workflow_drafts.py`` (budget): the CAS upsert serves the agent
draft-save tool — an ``expected_updated_at`` that no longer matches the
stored row raises ``DraftConflictError`` instead of overwriting a newer
draft. A row DELETED underneath the caller (publish deletes the draft row,
#1221) is instead silently recreated by the insert branch — allowed on
purpose (#1221 §4 方案 B). Constants (``DRAFT_NEVER_SAVED``) and the
exception stay importable from ``workflow_drafts`` for the existing import
surface.
"""

from __future__ import annotations

from typing import Any

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
        row exists at all, the plain INSERT always succeeds — a caller
        holding a stale timestamp whose row was deleted underneath it
        (publish deletes the draft row, #1221) silently recreates the draft,
        which reads as a brand-new draft rather than a conflict (#1221 §4
        方案 B: deliberately allowed). Unparseable timestamps never match —
        a malformed CAS token must not degrade into a blind overwrite.
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
        if row is None:
            current = self.get_workspace_workflow_draft(workspace_id)  # type: ignore[attr-defined]
            raise DraftConflictError(
                "Workflow draft was modified by another session"
                f" (expected updated_at {expected_updated_at},"
                f" current updated_at"
                f" {current['updated_at'] if current else None})"
            )
        return dict(row)
