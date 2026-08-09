from collections.abc import Mapping
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.executors.config import ExecutorConfig
from server.app.services.agent_service import published_agent_definitions
from server.app.services.executor_definition_service import published_executor_definitions
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def capability_detail(capability: str, config: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"name": capability}
    path = getattr(config, "path", None)
    timeout_seconds = getattr(config, "timeout_seconds", None)
    skill = getattr(config, "skill", None)
    tools = getattr(config, "tools", ())
    if path:
        detail["path"] = path
        detail["timeout_seconds"] = timeout_seconds
    if skill:
        detail["skill"] = skill
    if tools:
        detail["tools"] = list(tools)
    return detail


def executor_capability_detail(
    skills: SkillCatalogService,
    capability: str,
    config: Any,
) -> dict[str, Any]:
    detail = capability_detail(capability, config)
    skill = detail.get("skill")
    if isinstance(skill, str):
        detail.update(skills.metadata(skill))
    return detail


def execution_catalog(
    settings: Settings,
    skills: SkillCatalogService,
    agent_definitions: Mapping[str, AgentDefinition] | None = None,
    executor_definitions: Mapping[str, ExecutorConfig] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Catalog of executor + Agent definitions for Studio display.

    Both halves come from the published DB catalog (versioned_entities) so
    Studio shows the live DB values; ``settings.executor_definitions`` is the
    startup-hydrated snapshot of the same rows. The old global
    provider/model/thinking projection (retired ``workflows.pi``) is gone:
    execution defaults are workspace-scoped now, so the Studio "继承默认"
    hints read the workspace settings payload's agentDefaults instead.
    """
    if agent_definitions is None:
        agent_definitions = published_agent_definitions(settings.database_url)
    if executor_definitions is None:
        executor_definitions = published_executor_definitions(settings.database_url)
    return {
        "agents": [
            _agent_entry(skills, agent_id, definition)
            for agent_id, definition in sorted(agent_definitions.items())
        ],
        "executors": [
            {
                "id": executor_id,
                "kind": definition.kind,
                "global_capacity": definition.global_capacity,
                "capabilities": sorted(definition.capabilities),
                "capability_details": [
                    executor_capability_detail(skills, capability, config)
                    for capability, config in sorted(definition.capabilities.items())
                ],
            }
            for executor_id, definition in sorted(executor_definitions.items())
        ],
    }


def _agent_entry(
    skills: SkillCatalogService,
    agent_id: str,
    definition: AgentDefinition,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        **definition.model_dump(mode="json"),
        **skills.metadata(definition.skill),
    }


class ExecutorCatalogService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.skills = SkillCatalogService(settings.database_url)

    def catalog(self) -> dict[str, Any]:
        return execution_catalog(self.settings, self.skills)
