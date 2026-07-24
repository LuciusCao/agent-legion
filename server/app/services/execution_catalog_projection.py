from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.executor_catalog_detail import executor_capability_detail
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def execution_catalog(
    settings: Settings, skills: SkillCatalogService
) -> dict[str, list[dict[str, Any]]]:
    return {
        "agents": [
            _agent_entry(settings, skills, agent_id, definition)
            for agent_id, definition in sorted(settings.agent_definitions.items())
        ],
        "executors": [
            {
                "id": executor_id,
                "kind": definition.kind,
                "global_capacity": definition.global_capacity,
                "capabilities": sorted(definition.capabilities),
                "capability_details": [
                    executor_capability_detail(
                        settings, skills, definition.kind, capability, config
                    )
                    for capability, config in sorted(definition.capabilities.items())
                ],
            }
            for executor_id, definition in sorted(settings.executor_definitions.items())
        ],
    }


def _agent_entry(
    settings: Settings,
    skills: SkillCatalogService,
    agent_id: str,
    definition: AgentDefinition,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": agent_id,
        **definition.model_dump(mode="json"),
        **skills.metadata(definition.skill),
    }
    if definition.runtime == "pi":
        runtime = settings.executor_runtime.workflows.pi
        entry.update(
            provider=runtime.provider,
            model=runtime.model,
            thinking=runtime.thinking,
        )
    return entry
