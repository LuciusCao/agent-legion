from pydantic import BaseModel, Field


class SkillDirectoriesResponse(BaseModel):
    """Candidate skill directory names under ``<skills_root>/<workspace_id>/``
    (#327). Names only — content validation stays with the validate endpoint.
    The ``workspace_id`` query-param name is load-bearing: it is what
    require_workspace_access reads to reject non-members."""

    workspace_id: str
    directories: list[str] = Field(default_factory=list)
