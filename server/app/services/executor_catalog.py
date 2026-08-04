from typing import Any

from server.app.services.execution_catalog_projection import execution_catalog
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


class ExecutorCatalogService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.skills = SkillCatalogService(settings.root_dir)

    def catalog(self) -> dict[str, Any]:
        return execution_catalog(self.settings, self.skills)
