from typing import Any

from server.app.agent_catalog import AgentDefinition
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
    settings: Settings,
    skills: SkillCatalogService,
    kind: str,
    capability: str,
    config: Any,
) -> dict[str, Any]:
    detail = capability_detail(capability, config)
    skill = detail.get("skill")
    if isinstance(skill, str):
        detail.update(skills.metadata(skill))
    if kind == "pi":
        runtime = settings.executor_runtime.workflows.pi
        detail.update(
            provider=runtime.provider,
            model=runtime.model,
            thinking=runtime.thinking,
        )
    return detail


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
    if definition.runtime in ("pi", "velites"):
        runtime = settings.executor_runtime.workflows.pi
        entry.update(
            provider=runtime.provider,
            model=runtime.model,
            thinking=runtime.thinking,
        )
    return entry


class ExecutorCatalogService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.skills = SkillCatalogService(settings.root_dir)

    def catalog(self) -> dict[str, Any]:
        return execution_catalog(self.settings, self.skills)
