from fastapi import APIRouter

from ..agents import AgentStatusManager
from ..db import Database
from ..events import JobEventManager, VideoEventManager
from ..jobs import JobQueries
from ..settings import Settings
from ..worker_control import WorkerControl, WorkspaceWorkerControl
from .agents import create_agents_router
from .artifacts import create_artifacts_router
from .common import create_common_router
from .job_artifacts import create_job_artifacts_router
from .job_batches import create_job_batches_router
from .jobs import create_jobs_router
from .packages import create_packages_router
from .questions import create_questions_router
from .video_hive import create_video_hive_router
from .videos import create_videos_router
from .worker import create_worker_router
from .workflow_catalog import create_workflow_catalog_router
from .workspace_configuration import create_workspace_configuration_router
from .workspace_executors import create_workspace_executors_router
from .workspace_runs import create_workspace_runs_router
from .workspace_settings import create_workspace_settings_router
from .workspaces import create_workspaces_router


def create_router(
    db: Database,
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
    video_event_manager: VideoEventManager,
    worker_control: WorkerControl,
    workspace_worker_control: WorkspaceWorkerControl | None = None,
    job_event_manager: JobEventManager | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    from ..executors.leases import ExecutorLeaseRepository
    from ..services.executor_catalog import ExecutorCatalogService
    from ..services.job_artifact_mutation import JobArtifactMutationService
    from ..services.job_artifacts import JobArtifactService
    from ..services.job_deletion import JobDeletionService
    from ..services.job_execution import JobExecutionService
    from ..services.job_intake import JobIntakeService
    from ..services.job_logs import JobLogService
    from ..services.job_packages import JobPackageService
    from ..services.job_queries import JobQueryService
    from ..services.job_rerun import JobRerunService
    from ..services.package_deletion import PackageDeletionService
    from ..services.workflow_catalog import WorkflowCatalogService
    from ..services.workspace_configuration import WorkspaceConfigurationService
    from ..services.workspace_executor_configuration import WorkspaceExecutorConfigurationService

    workflow_catalog = WorkflowCatalogService(settings)
    executor_catalog = ExecutorCatalogService(settings)
    workspace_executor_configuration = WorkspaceExecutorConfigurationService(job_db)
    workspace_configuration = WorkspaceConfigurationService(
        job_db, settings, agent_manager, workflow_catalog
    )
    job_intake = JobIntakeService(
        job_db, settings, workflow_catalog, job_event_manager=job_event_manager
    )
    job_queries = JobQueryService(
        job_db, settings, workflow_catalog, workspace_executor_configuration
    )
    job_artifacts = JobArtifactService(job_db)
    job_logs = JobLogService(settings, job_db)
    executor_leases = ExecutorLeaseRepository(
        job_db.path, job_db=job_db, job_event_manager=job_event_manager
    )
    job_rerun = JobRerunService(
        job_db, executor_leases, settings, workflow_catalog, job_event_manager=job_event_manager
    )
    job_execution = JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        executor_leases,
        workflow_catalog,
        job_event_manager=job_event_manager,
    )
    job_deletion = JobDeletionService(
        job_db, executor_leases, settings, job_event_manager=job_event_manager
    )
    package_deletion = PackageDeletionService(db, settings.packages_dir)
    job_packages = JobPackageService(job_db, settings)

    router.include_router(create_common_router(db, settings, worker_control))
    router.include_router(create_agents_router(agent_manager))
    router.include_router(create_videos_router(db, settings, agent_manager, video_event_manager))
    router.include_router(create_artifacts_router(db, settings))
    router.include_router(
        create_packages_router(
            db, job_db, settings, video_event_manager, package_deletion, job_packages
        )
    )
    router.include_router(create_worker_router(worker_control, workspace_worker_control))
    router.include_router(create_workflow_catalog_router(workflow_catalog, settings))
    router.include_router(
        create_workspaces_router(
            workspace_configuration, settings, job_event_manager=job_event_manager
        )
    )
    router.include_router(create_workspace_settings_router(workspace_configuration, settings))
    router.include_router(create_workspace_configuration_router(workspace_configuration, settings))
    router.include_router(
        create_workspace_executors_router(
            executor_catalog, workspace_executor_configuration, settings
        )
    )
    router.include_router(create_job_batches_router(job_intake, settings))
    router.include_router(
        create_jobs_router(job_queries, job_rerun, job_deletion, job_execution, settings)
    )
    router.include_router(create_job_artifacts_router(job_artifacts, settings, job_logs))
    router.include_router(create_workspace_runs_router(job_queries, settings))
    router.include_router(create_video_hive_router(settings))
    router.include_router(create_questions_router(job_db, settings))

    return router
