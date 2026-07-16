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
