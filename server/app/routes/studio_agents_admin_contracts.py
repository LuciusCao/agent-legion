"""Admin contracts for the Studio chat ACP agent registry (phase 3 chunk 4).

The registry is the only way a chat session gets its agent command line:
admins maintain {id, label, command, args[]} entries here and non-admin users
pick an agent by id — arbitrary command lines never cross the non-admin API
boundary (RCE guard). api_base tells the session-scoped MCP server where the
backend's own API lives.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StudioAgentRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


class StudioAgentRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base: str = Field(min_length=1)
    agents: list[StudioAgentRegistryEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_agent_ids(self) -> StudioAgentRegistryDocument:
        ids = [agent.id for agent in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent id in studio agent registry")
        return self


class StudioAgentRegistryResponse(StudioAgentRegistryDocument):
    """Stored document plus a PATH-probe result per agent id.

    ``availability`` is response-only (admins see which entries can actually
    launch on this host); it is never persisted and never accepted on PUT,
    so it lives here rather than on the shared document model.
    """

    availability: dict[str, bool] = Field(default_factory=dict)


class StudioAgentRegistryUpdate(StudioAgentRegistryDocument):
    pass
