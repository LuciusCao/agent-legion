from __future__ import annotations

from fastapi import APIRouter

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.routes.job_artifacts import create_job_artifacts_router
from server.app.routes.job_batches import create_job_batches_router
from server.app.routes.job_invalid_paths import create_job_invalid_paths_router
from server.app.routes.job_workflow_upgrade import create_job_workflow_upgrade_router
from server.app.routes.jobs import create_jobs_router
from server.app.routes.questions import create_questions_router
from server.app.routes.token_usage import create_token_usage_router
from server.app.routes.video_jobs import create_video_jobs_router
from server.app.routes.workspace_runs import create_workspace_runs_router
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_intake import JobIntakeService
from server.app.services.job_logs import JobLogService
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings


def include_job_routes(
    router: APIRouter,
    job_db: JobQueries,
    settings: Settings,
    workflow_catalog: WorkflowCatalogService,
    workspace_executor_configuration: WorkspaceExecutorConfigurationService,
    job_event_manager: JobEventManager | None,
) -> None:
    executor_leases = ExecutorLeaseRepository(
        job_db.path, job_db=job_db, job_event_manager=job_event_manager
    )
    job_intake = JobIntakeService(
        job_db, settings, workflow_catalog, job_event_manager=job_event_manager
    )
    job_queries = JobQueryService(
        job_db, settings, workflow_catalog, workspace_executor_configuration
    )
    job_artifacts = JobArtifactService(job_db)
    job_logs = JobLogService(settings, job_db)
    job_rerun = JobRerunService(
        job_db, executor_leases, settings, workflow_catalog, job_event_manager=job_event_manager
    )
    job_workflow_upgrade = JobWorkflowUpgradeService(
        job_db, executor_leases, job_event_manager=job_event_manager
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

    router.include_router(create_job_batches_router(job_intake, settings))
    router.include_router(
        create_jobs_router(job_queries, job_rerun, job_deletion, job_execution, settings)
    )
    router.include_router(
        create_job_workflow_upgrade_router(job_queries, job_workflow_upgrade, settings)
    )
    router.include_router(create_job_artifacts_router(job_artifacts, settings, job_logs))
    router.include_router(create_video_jobs_router(job_db, settings))
    router.include_router(create_token_usage_router(job_queries, settings))
    router.include_router(create_job_invalid_paths_router(job_artifacts, settings))
    router.include_router(create_workspace_runs_router(job_queries, settings))
    router.include_router(create_questions_router(job_db, settings))
