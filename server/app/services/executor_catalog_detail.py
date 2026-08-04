from typing import Any

from server.app.services.executor_catalog_capabilities import capability_detail
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


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
