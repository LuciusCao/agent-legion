from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.routes.job_artifacts import create_job_artifacts_router
from server.app.routes.job_batches import create_job_batches_router
from server.app.routes.job_invalid_paths import create_job_invalid_paths_router
from server.app.routes.job_snapshot import create_job_snapshot_router
from server.app.routes.job_stress_events import create_job_stress_events_router
from server.app.routes.job_workflow_upgrade import create_job_workflow_upgrade_router
from server.app.routes.jobs import create_jobs_router
from server.app.routes.questions import create_questions_router
from server.app.routes.token_usage import create_token_usage_router
from server.app.routes.video_jobs import create_video_jobs_router
from server.app.routes.workspace_runs import create_workspace_runs_router
from server.app.services.job_service_factory import JobServices
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
    job_event_buffer: Any | None = None,
) -> None:
    services = JobServices(
        job_db,
        settings,
        workflow_catalog,
        workspace_executor_configuration,
        job_event_manager,
        job_event_buffer,
    )

    router.include_router(create_job_batches_router(services.intake, settings))
    router.include_router(
        create_jobs_router(
            services.queries,
            services.rerun,
            services.deletion,
            services.execution,
            settings,
        )
    )
    router.include_router(create_job_snapshot_router(services.patch_queries, settings))
    stress_router = create_job_stress_events_router(settings, job_event_buffer)
    if stress_router is not None:
        router.include_router(stress_router)
    router.include_router(
        create_job_workflow_upgrade_router(services.queries, services.workflow_upgrade, settings)
    )
    router.include_router(create_job_artifacts_router(services.artifacts, settings, services.logs))
    router.include_router(create_video_jobs_router(job_db, settings))
    router.include_router(create_token_usage_router(services.queries, settings))
    router.include_router(create_job_invalid_paths_router(services.artifacts, settings))
    router.include_router(create_workspace_runs_router(services.queries, settings))
    router.include_router(create_questions_router(job_db, settings))
