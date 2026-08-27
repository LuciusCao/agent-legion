"""Contracts for the studio-agent skill tools (issue #217).

``get_skill`` reuses ``SkillDetailResponse`` (same shape as the Studio
preview endpoint, including its 404-on-unknown-tag ``ref`` semantics).
Validation failures map to 422 with ``detail.errors`` carrying the same
``SkillValidationIssue`` list the validate endpoint returns.
"""

from pydantic import BaseModel, Field


class SkillValidationIssue(BaseModel):
    path: str
    error: str


class SkillValidateToolResponse(BaseModel):
    key: str
    valid: bool
    errors: list[SkillValidationIssue] = Field(default_factory=list)


class SkillVersionFileWrite(BaseModel):
    path: str = Field(min_length=1)
    content: str


class SkillSaveVersionRequest(BaseModel):
    files: list[SkillVersionFileWrite] = Field(min_length=1)
    new_tag: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SkillSaveVersionResponse(BaseModel):
    key: str
    tag: str
    commit: str
    files: list[str] = Field(default_factory=list)
