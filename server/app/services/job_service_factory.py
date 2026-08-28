from __future__ import annotations

from typing import Any

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_intake import JobIntakeService
from server.app.services.job_logs import JobLogService
from server.app.services.job_patch_queries import JobPatchQueryService
from server.app.services.job_pause import JobPauseService
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.run_service import RunService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings


class JobServices:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workspace_execution_configuration: WorkspaceExecutionConfigurationService,
        job_event_manager: JobEventManager | None,
        job_event_buffer: Any | None,
        artifact_store: ArtifactStore | None = None,
        object_store: Any = None,
    ) -> None:
        self.executor_leases = ExecutorLeaseRepository(
            job_db,
            data_dir=settings.data_dir,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.intake = JobIntakeService(
            job_db,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.runs = RunService(
            job_db,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.queries = JobQueryService(
            job_db, settings, workspace_execution_configuration, object_store=object_store
        )
        self.patch_queries = JobPatchQueryService(
            job_db, settings, job_event_buffer=job_event_buffer
        )
        self.artifacts = JobArtifactService(job_db, object_store=object_store)
        self.logs = JobLogService(settings, job_db)
        self.pause = JobPauseService(
            job_db,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.rerun = JobRerunService(
            job_db,
            self.executor_leases,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.workflow_upgrade = JobWorkflowUpgradeService(
            job_db,
            self.executor_leases,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.execution = JobExecutionService(
            job_db,
            JobArtifactMutationService(settings.jobs_dir),
            self.executor_leases,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
        self.deletion = JobDeletionService(
            job_db,
            self.executor_leases,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
            artifact_store=artifact_store,
            object_store=object_store,
        )
