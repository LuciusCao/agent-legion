from typing import Any

from server.app.executors.local_handler_loader import build_local_handlers
from server.app.executors.pi import build_skill_manager
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.services.artifact_store import ArtifactStore
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
        skill_manager = build_skill_manager(settings.root_dir)
    runtime = RuntimeDependencies(
        pi_runtime=settings.executor_runtime.workflows.pi,
        skill_manager=skill_manager,
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        resource_providers=settings.resource_providers,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
        artifact_store=artifact_store,
        repo_root=settings.root_dir,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)
