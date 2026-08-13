from typing import Any

from server.app.executors.pi import build_skill_manager
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.services.artifact_store import ArtifactStore
from server.app.services.executor_definition_service import published_executor_definitions
from server.app.settings import Settings
from server.app.skills.manager import SkillManager


def build_executor_registry(
    settings: Settings,
    job_db: Any | None = None,
    artifact_store: ArtifactStore | None = None,
    skill_manager: SkillManager | None = None,
) -> ExecutorRegistry:
    """Build the application-wide executor registry from settings (once per app)."""
    if skill_manager is None:
        skill_manager = build_skill_manager(settings.database_url)
    runtime = RuntimeDependencies(
        pi_runtime=settings.executor_runtime.workflows.pi,
        skill_manager=skill_manager,
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
        artifact_store=artifact_store,
        repo_root=settings.root_dir,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)


def reload_published_executors(settings: Settings, registry: ExecutorRegistry) -> None:
    """Hot-apply the published executor catalog after publish/rollback/archive.

    Adapters are rebuilt and validated before the single state swap: a build
    failure leaves the running registry and settings untouched. The write
    paths invalidate the published cache first, so this read is fresh.
    """
    definitions = published_executor_definitions(settings.database_url)
    registry.replace_definitions(definitions)
    settings.executor_definitions = definitions
