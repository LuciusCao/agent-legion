"""Router dependency bundle for the API router tree (issue #189).

``RouterDeps`` replaces the former 22-parameter ``create_router`` signature:
a missing required service now fails at construction (TypeError) instead of
silently dropping a whole route group. Optional integration seams stay
``None``-able — they represent genuinely absent infrastructure (reduced
embeds, object storage off), not wiring mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_control import AgentCompletionHandler, AgentWorkerRegistry
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_packages import JobPackageService
from server.app.services.materials import MaterialsService
from server.app.services.ops_metrics import OpsMetricsService
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_replays import QualityReplayService
from server.app.services.quality_sampling import QualitySamplingService
from server.app.services.quality_stats import QualityStatsService
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
from server.app.studio_chat.service import StudioChatService
from server.app.worker_control import WorkspaceWorkerControl


@dataclass
class RouterDeps:
    """Explicit, complete dependency bundle for the API router tree."""

    job_db: JobQueries
    settings: Settings
    agent_manager: AgentStatusManager
    executor_catalog: ExecutorCatalogService
    workspace_executor_configuration: WorkspaceExecutorConfigurationService
    workspace_configuration: WorkspaceConfigurationService
    job_packages: JobPackageService
    # Optional integration seams: genuinely absent infrastructure (reduced
    # embeds, object storage off) — not wiring mistakes.
    workspace_worker_control: WorkspaceWorkerControl | None = None
    job_event_manager: JobEventManager | None = None
    job_event_buffer: Any | None = None
    artifact_store: ArtifactStore | None = None
    agent_broker: AgentExecutionBroker | None = None
    agent_worker_registry: AgentWorkerRegistry | None = None
    agent_completion: AgentCompletionHandler | None = None
    ops_metrics: OpsMetricsService | None = None
    quality_sampling: QualitySamplingService | None = None
    quality_labels: QualityLabelService | None = None
    quality_stats: QualityStatsService | None = None
    quality_replays: QualityReplayService | None = None
    studio_chat_service: StudioChatService | None = None
    materials_service: MaterialsService | None = None
    job_artifact_objects: Any | None = None
