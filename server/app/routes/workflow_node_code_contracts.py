from typing import Literal

from pydantic import BaseModel


class WorkflowNodeCodeResponse(BaseModel):
    # builtin = a system-seeded workspace version; custom = a user-published
    # workspace version; none = start from the SDK template.
    origin: Literal["builtin", "custom", "none"]
    code: str
    # Published user version serving the node (origin=custom only).
    version: int | None = None
    has_draft: bool = False
    # Current draft content, when one exists (drafts are editable user data).
    draft_code: str | None = None
    draft_version: int | None = None
    # #749: the current draft's code_hash — the CAS token the inspector
    # panel carries back as expected_hash on publish (same pattern as the
    # #692 chat draft cards: the publisher must assert the identity of the
    # draft it saw). None when no draft exists.
    draft_code_hash: str | None = None
    # Instance-level byte budget for one code version (#628): the Studio
    # editor and the studio-agent draft loop display/self-check against it.
    # Read-only, server-injected; the default keeps old clients rendering.
    max_code_bytes: int = 64 * 1024


class WorkflowNodeCodeTemplateResponse(BaseModel):
    code: str


class WorkflowNodeCodeDraftRequest(BaseModel):
    code: str
    change_note: str | None = None


class WorkflowNodeCodePublishRequest(BaseModel):
    """#692 codex P1: the caller's asserted draft code_hash — verified
    atomically inside the publish transaction; mismatch raises 409 with
    zero publish side effects. Absent (legacy callers) keeps the old
    no-check semantics."""

    expected_hash: str | None = None


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
