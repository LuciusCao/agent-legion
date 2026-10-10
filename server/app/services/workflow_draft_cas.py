"""Compare-and-set save for the Studio workflow YAML draft (#633).

Split from ``workflow_draft_store.py`` (budget): the CAS save serves the
agent tool surface AND (#633 codex review P1-1) the human editor's PUT when
it carries ``expected_updated_at`` — the caller passes the ``updated_at`` it
last read; a stored draft that moved on raises a conflict whose payload
carries the CURRENT draft (so the losing writer can rebase without a second
round-trip) instead of silently overwriting the newer draft.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.workflow_drafts import (
    DraftConflictError as CasDraftConflictError,
)
from server.app.services.job_errors import (
    DraftConflictError,
    InvalidDraftCasTokenError,
    NotFoundError,
)
from server.app.services.workflow_draft_cas_token import (
    CAS_TIMESTAMP_HINT,
    parse_cas_timestamp,
)
from server.app.services.workflow_draft_store import (
    attach_draft_identity_hash,
    get_workflow_draft,
)


def _conflict_payload(expected_updated_at: str, current: dict[str, Any] | None) -> dict[str, Any]:
    """409 detail 的契约化构造（#1177 codex P1）：字段集与
    ``workflow_draft_store_contracts.WorkflowDraftConflictDetail``（路由侧
    OpenAPI responses= 声明的模型）逐字段一致——路由测试以模型 dump 为
    oracle 钉死两者同步，service 层不 import routes 包（分层方向）。
    #1143: current_draft carries the semantic identity hash — the adopting
    side (frontend adopt path) restores savedHash from it.
    """
    return {
        "message": (
            "Workflow draft conflict: another session saved a newer"
            " draft. Re-read the draft, rebase your changes and retry."
        ),
        "expected_updated_at": expected_updated_at,
        "current_draft": {
            "definition_yaml": current["definition_yaml"] if current else None,
            "updated_at": str(current["updated_at"]) if current else None,
            "definition_hash": current.get("definition_hash") if current else None,
        },
    }


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
    round-trip. A token that is neither the marker nor a parseable ISO
    timestamp is a service-level ``InvalidDraftCasTokenError`` (422) — the
    timestamptz cast must never see it as a DB error (500); both contracts
    pre-validate with the shared ``parse_cas_timestamp``.
    """
    if not parse_cas_timestamp(expected_updated_at):
        raise InvalidDraftCasTokenError(CAS_TIMESTAMP_HINT)
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    try:
        draft = attach_draft_identity_hash(
            job_db.upsert_workspace_workflow_draft_if_unchanged(
                workspace_id, definition_yaml, expected_updated_at
            )
        )
    except CasDraftConflictError as exc:
        # #1143: current_draft carries the semantic identity hash too — the
        # adopting side (frontend adopt path) restores savedHash from it.
        # #1177 codex P1: payload is contract-shaped (see _conflict_payload).
        current = get_workflow_draft(job_db, workspace_id)
        raise DraftConflictError(_conflict_payload(expected_updated_at, current)) from exc
    assert draft is not None  # the CAS upsert path returns a row or raises
    return draft
