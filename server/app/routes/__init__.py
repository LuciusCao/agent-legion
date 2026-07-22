from typing import Any

from fastapi import APIRouter

from ..agent_broker import AgentExecutionBroker
from ..agent_completion import AgentCompletionHandler
from ..agent_workers import AgentWorkerRegistry
from ..agents import AgentStatusManager
from ..db import Database
from ..events import JobEventManager
from ..jobs import JobQueries
from ..services.artifact_store import ArtifactStore
from ..services.executor_catalog import ExecutorCatalogService
from ..services.job_packages import JobPackageService
from ..services.package_deletion import PackageDeletionService
from ..services.workflow_catalog import WorkflowCatalogService
from ..services.workspace_configuration import WorkspaceConfigurationService
from ..services.workspace_executor_configuration import WorkspaceExecutorConfigurationService
from ..settings import Settings
from ..worker_control import WorkspaceWorkerControl
from .agent_workers import create_agent_workers_router
from .agents import create_agents_router
from .artifacts import create_artifacts_router
from .common import create_common_router
from .job_route_group import include_job_routes
from .packages import create_packages_router
from .worker import create_worker_router
from .workflow_catalog import create_workflow_catalog_router
from .workflow_resource_providers import create_workflow_resource_providers_router
from .workflow_revisions import create_workflow_revisions_router
from .workspace_agent_routes import create_workspace_agent_routes_router
from .workspace_configuration import create_workspace_configuration_router
from .workspace_executors import create_workspace_executors_router
from .workspace_settings import create_workspace_settings_router
from .workspaces import create_workspaces_router


def create_router(
    db: Database,
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
    workspace_worker_control: WorkspaceWorkerControl | None = None,
    *,
    workflow_catalog: WorkflowCatalogService,
    executor_catalog: ExecutorCatalogService,
    workspace_executor_configuration: WorkspaceExecutorConfigurationService,
    workspace_configuration: WorkspaceConfigurationService,
    package_deletion: PackageDeletionService,
    job_packages: JobPackageService,
    job_event_manager: JobEventManager | None = None,
    job_event_buffer: Any | None = None,
    artifact_store: ArtifactStore | None = None,
    agent_broker: AgentExecutionBroker | None = None,
    agent_worker_registry: AgentWorkerRegistry | None = None,
    agent_completion: AgentCompletionHandler | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    router.include_router(create_common_router(db, settings))
    router.include_router(create_agents_router(agent_manager))
    router.include_router(
        create_packages_router(db, job_db, settings, package_deletion, job_packages)
    )
    router.include_router(create_worker_router(workspace_worker_control))
    if (
        agent_broker is not None
        and agent_worker_registry is not None
        and agent_completion is not None
    ):
        router.include_router(
            create_agent_workers_router(
                agent_broker, agent_worker_registry, agent_completion, settings
            )
        )
    if artifact_store is not None:
        router.include_router(
            create_artifacts_router(artifact_store, settings, agent_worker_registry)
        )
    router.include_router(create_workflow_catalog_router(workflow_catalog, settings))
    router.include_router(create_workflow_resource_providers_router(workflow_catalog, settings))
    router.include_router(
        create_workspaces_router(
            workspace_configuration, settings, job_event_manager=job_event_manager
        )
    )
    router.include_router(create_workspace_settings_router(workspace_configuration, settings))
    router.include_router(create_workflow_revisions_router(job_db, settings))
    router.include_router(create_workspace_configuration_router(workspace_configuration, settings))
    router.include_router(
        create_workspace_executors_router(
            executor_catalog, workspace_executor_configuration, settings
        )
    )
    router.include_router(create_workspace_agent_routes_router(job_db, settings))
    include_job_routes(
        router,
        job_db,
        settings,
        workflow_catalog,
        workspace_executor_configuration,
        job_event_manager,
        job_event_buffer,
        artifact_store=artifact_store,
    )

    return router
