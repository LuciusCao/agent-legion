"""Service layer for the Studio workflow YAML draft store (schema v61).

Thin pass-through over JobQueries (BOUNDARY-DATA-001): the only business
rule here is that the workspace must exist (404, mirroring the workflow
revisions routes) — the draft row itself is created by the upsert.

The #633 compare-and-set save lives in ``workflow_draft_cas.py`` (split
for the budget); ``DRAFT_NEVER_SAVED`` is re-exported here so routes and
clients depend on this module only.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.workflow_drafts import DRAFT_NEVER_SAVED as _NEVER_SAVED
from server.app.services.job_errors import NotFoundError
from server.app.services.workflow_drafts import workflow_draft_identity_hash

__all__ = [
    "DRAFT_NEVER_SAVED",
    "attach_draft_identity_hash",
    "get_workflow_draft",
    "save_workflow_draft",
]

DRAFT_NEVER_SAVED = _NEVER_SAVED


def attach_draft_identity_hash(draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """#1143：draft 行补上语义身份 hash（不可解析 → None），响应契约直接
    model_validate 拾取；GET/PUT/工具面/409 current_draft 共用同一身份源。"""
    if draft is None:
        return None
    return {
        **draft,
        "definition_hash": workflow_draft_identity_hash(str(draft["definition_yaml"])),
    }


def get_workflow_draft(job_db: JobQueries, workspace_id: str) -> dict[str, Any] | None:
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    return attach_draft_identity_hash(job_db.get_workspace_workflow_draft(workspace_id))


def save_workflow_draft(
    job_db: JobQueries, workspace_id: str, definition_yaml: str
) -> dict[str, Any]:
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    draft = attach_draft_identity_hash(
        job_db.upsert_workspace_workflow_draft(workspace_id, definition_yaml)
    )
    assert draft is not None  # the upsert always returns a row
    return draft
