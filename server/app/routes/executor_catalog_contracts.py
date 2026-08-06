from typing import Literal

from pydantic import BaseModel, Field

from server.app.routes.agent_catalog_contracts import AgentDefinitionResponse


class ExecutorCapabilityResponse(BaseModel):
    name: str
    path: str | None = None
    timeout_seconds: int | None = None
    skill: str | None = None
    tools: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    skill_ref: str | None = None
    skill_commit: str | None = None


class ExecutorDefinitionResponse(BaseModel):
    id: str
    kind: Literal["code", "pi", "openclaw"]
    global_capacity: int = Field(ge=1)
    capabilities: list[str]
    capability_details: list[ExecutorCapabilityResponse] = Field(default_factory=list)


class ExecutorCatalogResponse(BaseModel):
    executors: list[ExecutorDefinitionResponse]
    agents: list[AgentDefinitionResponse] = Field(default_factory=list)
