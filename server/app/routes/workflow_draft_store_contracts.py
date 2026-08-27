"""Contracts for the Studio workflow YAML draft store (schema v61).

Empty-state convention: GET always returns 200 for an existing workspace,
with ``definition_yaml: null`` / ``updated_at: null`` when no draft was ever
saved — same structured-empty style as the revisions list (``revisions: []``)
rather than a 404, so the editor's first load needs no error-branch handling.
"""

from pydantic import BaseModel, field_validator


class WorkflowDraftStoreRequest(BaseModel):
    definition_yaml: str

    @field_validator("definition_yaml")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # A blank draft is never meaningful (the editor's draft always
        # carries at least a workflow key); refuse it instead of storing
        # content that would resurrect as a broken draft on the next load.
        if not value.strip():
            raise ValueError("definition_yaml must not be blank")
        return value


class WorkflowDraftStoreResponse(BaseModel):
    definition_yaml: str | None = None
    updated_at: str | None = None
