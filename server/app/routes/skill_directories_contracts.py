from pydantic import BaseModel, Field


class SkillDirectoriesResponse(BaseModel):
    """Candidate skill directory names under ``<skills_root>/<scope>/``
    (#327). Names only — content validation stays with the validate endpoint."""

    scope: str
    directories: list[str] = Field(default_factory=list)
