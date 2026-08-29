from typing import Literal

from pydantic import BaseModel, Field


class AgentDefinitionResponse(BaseModel):
    id: str
    runtime: Literal["pi", "openclaw", "velites"]
    capability: str
    skill: str
    tools: list[str] = Field(default_factory=list)
    requires_labels: dict[str, str] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    skill_ref: str | None = None
    skill_commit: str | None = None


class AgentCatalogResponse(BaseModel):
    """Agent catalog for Studio (issue #198: agents-only semantics).

    The former ``executors`` half retired with the executor concept
    (schema v47, EXEC-CODE-POOL-001); the endpoint was renamed from
    ``/api/executors`` to match the agents-only meaning.
    """

    agents: list[AgentDefinitionResponse] = Field(default_factory=list)
