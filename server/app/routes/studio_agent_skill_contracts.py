"""Contracts for the studio-agent skill tools (issue #217, warnings #542).

``get_skill`` reuses ``SkillDetailResponse`` (same shape as the Studio
preview endpoint, including its 404-on-unknown-tag ``ref`` semantics).
Validation failures map to 422 with ``detail.errors`` carrying the same
``SkillValidationIssue`` list the validate endpoint returns; ``warnings``
carries the #542 missing-contract notices (a save that succeeds despite
them reports them the same way).
"""

from pydantic import BaseModel, Field


class SkillValidationIssue(BaseModel):
    path: str
    error: str


class SkillValidationWarning(BaseModel):
    path: str
    error: str


class SkillValidateToolResponse(BaseModel):
    key: str
    valid: bool
    errors: list[SkillValidationIssue] = Field(default_factory=list)
    # #542: e.g. the missing-root-contract.yaml migration nudge; the
    # verdict stays ``valid`` (warning, not error).
    warnings: list[SkillValidationWarning] = Field(default_factory=list)


class SkillVersionFileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    # Aligned with the 128 KB read cap (skill_repo.MAX_FILE_BYTES).
    content: str = Field(max_length=128 * 1024)


class SkillSaveVersionRequest(BaseModel):
    files: list[SkillVersionFileWrite] = Field(min_length=1, max_length=100)
    new_tag: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4096)


class SkillSaveVersionResponse(BaseModel):
    key: str
    tag: str
    commit: str
    files: list[str] = Field(default_factory=list)
    # Shared materials the save synced into the commit (#633); empty list
    # when the workspace has no _shared mapping for this skill.
    synced_files: list[str] = Field(default_factory=list)
    # #542: the save succeeded despite these (missing root contract.yaml —
    # migration-window warning, not an error).
    warnings: list[SkillValidationWarning] = Field(default_factory=list)


class SkillCreateRequest(BaseModel):
    """create_skill payload (#633): same bounds as SkillSaveVersionRequest."""

    skill_name: str = Field(min_length=1, max_length=64)
    files: list[SkillVersionFileWrite] = Field(min_length=1, max_length=100)
    new_tag: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4096)


class SkillCreateResponse(BaseModel):
    key: str
    tag: str
    commit: str
