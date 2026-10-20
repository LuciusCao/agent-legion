"""Composition-root wiring for the background task group (#946).

Pulled out of ``server.app.main`` (file budget — the composition root sits at
its ceiling band): BackgroundTasks assembly (intake consumer, runs-async
feeder, ops metrics, batch-ops feeder) lives here. The batch-ops feeder
replays the sync batch services job by job, so it gets its own JobServices
bundle (same construction args the job route group uses) plus the app's one
JobPackageService instance.
"""

from __future__ import annotations

from typing import Any

from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.aggregator import WorkspaceJobEventAggregator
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_batch_operations.feeder import JobBatchOpsFeeder
from server.app.services.job_batch_operations.handlers import BatchOpsExecutors
from server.app.services.job_intake_queue import JobIntakeQueue
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.materials import MaterialsService
from server.app.services.ops_metrics import OpsMetricsService
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings
from server.app.startup_tasks import BackgroundTasks


def build_job_batch_ops_feeder(
    job_db: JobQueries,
    settings: Settings,
    job_event_manager: JobEventManager | None,
    job_event_buffer: Any | None,
    *,
    artifact_store: ArtifactStore | None,
    object_store: Any,
    job_packages: JobPackageService,
) -> JobBatchOpsFeeder:
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        job_event_manager,
        job_event_buffer,
        artifact_store=artifact_store,
        object_store=object_store,
    )
    return JobBatchOpsFeeder(
        job_db,
        settings,
        BatchOpsExecutors(
            rerun=services.rerun,
            deletion=services.deletion,
            execution=services.execution,
            pause=services.pause,
            upgrade=services.workflow_upgrade,
            packages=job_packages,
        ),
    )


def build_background_tasks(
    job_db: JobQueries,
    settings: Settings,
    workspace_event_aggregator: WorkspaceJobEventAggregator,
    agent_manager: AgentStatusManager,
    ops_metrics: OpsMetricsService,
    job_event_manager: JobEventManager,
    job_event_buffer: Any,
    materials_service: MaterialsService,
    artifact_store: ArtifactStore,
    object_store: Any,
    job_packages: JobPackageService,
) -> BackgroundTasks:
    return BackgroundTasks(
        workspace_event_aggregator=workspace_event_aggregator,
        agent_broadcast_controller=agent_manager.broadcast_controller,
        job_intake_queue=JobIntakeQueue(job_db, settings, job_event_buffer),
        ops_metrics=ops_metrics,
        # #1117: runs-async items feeder shares the one MaterialsService
        # instance (text-item materialization).
        run_batch_feeder=RunBatchFeeder(
            job_db, settings, job_event_buffer, materials_service=materials_service
        ),
        # #946: batch-ops feeder.
        job_batch_ops_feeder=build_job_batch_ops_feeder(
            job_db,
            settings,
            job_event_manager,
            job_event_buffer,
            artifact_store=artifact_store,
            object_store=object_store,
            job_packages=job_packages,
        ),
    )
