from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.services.agent_service import published_agent_definitions
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def agent_catalog(
    settings: Settings,
    skills: SkillCatalogService,
    workspace_id: str,
    agent_definitions: Mapping[str, AgentDefinition] | None = None,
    connect_source: ConnectSource | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Catalog of Agent definitions for Studio display (P-0.5: Agents only).

    The Agent half is workspace-scoped (schema v46): Studio shows the
    workspace's own published definitions, with no global fallback. The
    executors half retired with the executor concept (schema v47). Execution
    defaults are workspace-scoped: the Studio "继承默认" hints read the
    workspace settings payload's agentDefaults.
    """
    if agent_definitions is None:
        agent_definitions = published_agent_definitions(
            connect_source or settings.database_url, workspace_id
        )
    return {
        "agents": [
            {
                "id": agent_id,
                **definition.model_dump(mode="json"),
                **skills.metadata(definition.skill),
            }
            for agent_id, definition in sorted(agent_definitions.items())
        ],
    }


class AgentCatalogService:
    """Agents-only catalog for Studio (#198). ``connect_source`` is the
    JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187)."""

    def __init__(self, settings: Settings, connect_source: ConnectSource | None = None) -> None:
        self.settings = settings
        self._connect_source = connect_source or settings.database_url
        self.skills = SkillCatalogService(self._connect_source)

    def catalog(self, workspace_id: str) -> dict[str, Any]:
        return agent_catalog(
            self.settings, self.skills, workspace_id, connect_source=self._connect_source
        )
