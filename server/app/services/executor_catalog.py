from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import published_agent_definitions
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def execution_catalog(
    settings: Settings,
    skills: SkillCatalogService,
    workspace_id: str,
    agent_definitions: Mapping[str, AgentDefinition] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Catalog of Agent definitions for Studio display (P-0.5: Agents only).

    The Agent half is workspace-scoped (schema v46): Studio shows the
    workspace's own published definitions, with no global fallback. The
    executors half retired with the executor concept (schema v47). Execution
    defaults are workspace-scoped: the Studio "继承默认" hints read the
    workspace settings payload's agentDefaults.
    """
    if agent_definitions is None:
        agent_definitions = published_agent_definitions(settings.database_url, workspace_id)
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


class ExecutorCatalogService:
    """Agents-only execution catalog (keeps the pre-retirement name until step 3)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.skills = SkillCatalogService(settings.database_url)

    def catalog(self, workspace_id: str) -> dict[str, Any]:
        return execution_catalog(self.settings, self.skills, workspace_id)
