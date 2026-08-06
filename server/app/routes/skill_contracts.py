from pydantic import BaseModel, Field


class SkillFileResponse(BaseModel):
    path: str
    size: int = Field(ge=0)
    content: str
    truncated: bool = False


class SkillDetailResponse(BaseModel):
    key: str
    ref: str
    commit: str
    available: bool
    files: list[SkillFileResponse] = Field(default_factory=list)


class SkillValidateRequest(BaseModel):
    path: str = Field(min_length=1)


class SkillValidateResponse(BaseModel):
    valid: bool
    path: str
    skill_key: str | None = None
    error: str | None = None
    tags: list[str] = Field(default_factory=list)
    latest_tag: str | None = None
    locked_ref: str | None = None


class SkillTagsResponse(BaseModel):
    path: str
    tags: list[str] = Field(default_factory=list)
    latest_tag: str | None = None
