from typing import Any

from server.app.executors._pi_skill import build_skill_manager
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.local_handler_loader import build_local_handlers
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings


def build_executor_registry(
    settings: Settings,
    job_db: Any | None = None,
    artifact_store: ArtifactStore | None = None,
) -> ExecutorRegistry:
    """Build the application-wide executor registry from settings (once per app)."""
    skill_manager = build_skill_manager(settings.root_dir)
    runtime = RuntimeDependencies(
        local_handlers=build_local_handlers(settings),
        pi_runtime=settings.executor_runtime.workflows.pi,
        skill_manager=skill_manager,
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        resource_providers=settings.resource_providers,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
        artifact_store=artifact_store,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)
