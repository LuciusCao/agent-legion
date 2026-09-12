"""Compare-and-set save for the Studio workflow YAML draft (#633).

Split from ``workflow_draft_store.py`` (budget): the CAS save serves the
agent tool surface — the caller passes the ``updated_at`` it last read; a
stored draft that moved on raises a conflict whose payload carries the
CURRENT draft (so the agent can rebase without a second round-trip)
instead of silently overwriting the newer draft.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.workflow_drafts import (
    DraftConflictError as CasDraftConflictError,
)
from server.app.services.job_errors import DraftConflictError, NotFoundError


def save_workflow_draft_if_unchanged(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    expected_updated_at: str,
) -> dict[str, Any]:
    """CAS save (#633): 409 with the current draft when the base moved on.

    ``expected_updated_at`` is the ``updated_at`` string a previous read
    returned, or ``workflow_draft_store.DRAFT_NEVER_SAVED`` when the caller
    saw the structured empty state (no row yet). Conflict payloads attach
    the current draft so the losing writer can rebase and retry in one
    round-trip.
    """
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    try:
        return job_db.upsert_workspace_workflow_draft_if_unchanged(
            workspace_id, definition_yaml, expected_updated_at
        )
    except CasDraftConflictError as exc:
        current = job_db.get_workspace_workflow_draft(workspace_id)
        raise DraftConflictError(
            {
                "message": (
                    "Workflow draft conflict: another session saved a newer"
                    " draft. Re-read the draft, rebase your changes and retry."
                ),
                "expected_updated_at": expected_updated_at,
                "current_draft": {
                    "definition_yaml": current["definition_yaml"] if current else None,
                    "updated_at": current["updated_at"] if current else None,
                },
            }
        ) from exc
