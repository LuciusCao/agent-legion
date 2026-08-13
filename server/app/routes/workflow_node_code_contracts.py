from typing import Literal

from pydantic import BaseModel


class WorkflowNodeCodeResponse(BaseModel):
    origin: Literal["builtin", "custom", "none"]
    code: str
    # Repo-relative path of the builtin file (origin=builtin only).
    path: str | None = None
    # Published custom version serving the node (origin=custom only).
    version: int | None = None
    has_draft: bool = False
    # Current draft content, when one exists (drafts are editable user data).
    draft_code: str | None = None
    draft_version: int | None = None


class WorkflowNodeCodeTemplateResponse(BaseModel):
    code: str


class WorkflowNodeCodeDraftRequest(BaseModel):
    code: str
    change_note: str | None = None


class WorkflowNodeCodeVersionResponse(BaseModel):
    id: str
    version: int
    status: str
    code: str
    code_hash: str
    created_by: str
    change_note: str | None = None
    created_at: str
    published_at: str | None = None


class WorkflowNodeCodeVersionSummary(BaseModel):
    id: str
    version: int
    status: str
    code_hash: str
    created_by: str
    change_note: str | None = None
    created_at: str
    published_at: str | None = None


class WorkflowNodeCodeVersionsResponse(BaseModel):
    versions: list[WorkflowNodeCodeVersionSummary]


class WorkflowNodeCodeRollbackRequest(BaseModel):
    version: int


class WorkflowNodeCodeArchiveResponse(BaseModel):
    archived: int
