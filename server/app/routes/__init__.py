from typing import Any

from fastapi import APIRouter, Depends

from ..agent_broker import AgentExecutionBroker
from ..agent_completion import AgentCompletionHandler
from ..agent_workers import AgentWorkerRegistry
from ..auth.dependencies import require_workspace_access
from ..events import JobEventManager
from ..events.agents import AgentStatusManager
from ..jobs import JobQueries
from ..services.artifact_store import ArtifactStore
from ..services.executor_catalog import ExecutorCatalogService
from ..services.job_packages import JobPackageService
from ..services.ops_metrics import OpsMetricsService
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
from .metrics import create_metrics_router
from .packages import create_packages_router
from .token_usage_pricing import create_token_usage_pricing_router
from .worker import create_worker_router
from .workflow_catalog import create_workflow_catalog_router
from .workflow_node_codes import create_workflow_node_codes_router
from .workflow_node_files import create_workflow_node_files_router
from .workflow_revisions import create_workflow_revisions_router
from .workspace_agent_routes import create_workspace_agent_routes_router
from .workspace_configuration import create_workspace_configuration_router
from .workspace_executors import create_workspace_executors_router
from .workspace_settings import create_workspace_settings_router
from .workspaces import create_workspaces_router


def create_router(
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
    workspace_worker_control: WorkspaceWorkerControl | None = None,
    *,
    workflow_catalog: WorkflowCatalogService,
    executor_catalog: ExecutorCatalogService,
    workspace_executor_configuration: WorkspaceExecutorConfigurationService,
    workspace_configuration: WorkspaceConfigurationService,
    job_packages: JobPackageService,
    job_event_manager: JobEventManager | None = None,
    job_event_buffer: Any | None = None,
    artifact_store: ArtifactStore | None = None,
    agent_broker: AgentExecutionBroker | None = None,
    agent_worker_registry: AgentWorkerRegistry | None = None,
    agent_completion: AgentCompletionHandler | None = None,
    ops_metrics: OpsMetricsService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def secured(sub_router: APIRouter) -> None:
        router.include_router(sub_router, dependencies=[Depends(require_workspace_access)])

    router.include_router(create_common_router())
    router.include_router(create_agents_router(agent_manager))
    router.include_router(create_token_usage_pricing_router(job_db, settings))
    # Global admin endpoint (not workspace-scoped): the sub-router enforces
    # require_admin itself, so it must not go through secured().
    router.include_router(create_workflow_node_files_router(settings))
    secured(create_packages_router(job_db, settings, job_packages))
    secured(create_worker_router(workspace_worker_control))
    if (
        agent_broker is not None
        and agent_worker_registry is not None
        and agent_completion is not None
    ):
        workers_router = create_agent_workers_router(agent_broker, agent_worker_registry, agent_completion, settings, ops_metrics)  # fmt: skip
        router.include_router(workers_router)
    if artifact_store is not None:
        router.include_router(
            create_artifacts_router(artifact_store, settings, agent_worker_registry)
        )
    if ops_metrics is not None:
        secured(create_metrics_router(ops_metrics))
    secured(create_workflow_catalog_router(workflow_catalog, settings))
    workspaces_router = create_workspaces_router(
        workspace_configuration, settings, job_event_manager=job_event_manager
    )
    secured(workspaces_router)
    secured(create_workspace_settings_router(workspace_configuration, settings))
    secured(create_workflow_revisions_router(job_db, settings))
    secured(create_workflow_node_codes_router(job_db, settings))
    secured(create_workspace_configuration_router(workspace_configuration, settings))
    executors_router = create_workspace_executors_router(
        executor_catalog, workspace_executor_configuration, settings
    )
    secured(executors_router)
    secured(create_workspace_agent_routes_router(job_db, settings))
    job_group = APIRouter(dependencies=[Depends(require_workspace_access)])
    include_job_routes(
        job_group,
        job_db,
        settings,
        workflow_catalog,
        workspace_executor_configuration,
        job_event_manager,
        job_event_buffer,
        artifact_store=artifact_store,
    )
    router.include_router(job_group)

    return router
