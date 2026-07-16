from typing import Any

from server.app.services.executor_catalog_detail import executor_capability_detail
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


class ExecutorCatalogService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.skills = SkillCatalogService(settings.root_dir)

    def catalog(self) -> dict[str, Any]:
        return {
            "executors": [
                {
                    "id": executor_id,
                    "kind": definition.kind,
                    "global_capacity": definition.global_capacity,
                    "capabilities": sorted(definition.capabilities),
                    "capability_details": [
                        executor_capability_detail(
                            self.settings, self.skills, definition.kind, capability, config
                        )
                        for capability, config in sorted(definition.capabilities.items())
                    ],
                }
                for executor_id, definition in sorted(self.settings.executor_definitions.items())
            ]
        }
