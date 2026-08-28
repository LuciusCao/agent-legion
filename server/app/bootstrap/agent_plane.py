"""Agent execution plane assembly (issue #190).

One builder for the objects that execute agent-routed nodes end to end:
the broker (queue + leases), the dispatch service (bundle production), the
worker registry (registration/auth), and the completion handler (result
commit). Extracted from ``main.py`` so the composition root reads as domain
groups instead of 25 inline constructors.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.app.agent_broker import AgentDispatchService, AgentExecutionBroker
from server.app.agent_control import AgentCompletionHandler, AgentWorkerRegistry
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.buffer import JobEventBuffer
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.settings import Settings
from server.app.skills.runtime import build_skill_manager
from server.app.worker_control import WorkspaceWorkerControl


@dataclass
class AgentPlane:
    """The agent execution surface and its shared collaborators."""

    broker: AgentExecutionBroker
    dispatch: AgentDispatchService
    worker_registry: AgentWorkerRegistry
    completion: AgentCompletionHandler
    executor_leases: ExecutorLeaseRepository


def build_agent_plane(
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
    workspace_worker_control: WorkspaceWorkerControl,
    artifact_store: ArtifactStore,
    job_event_manager: JobEventManager,
    job_event_buffer: JobEventBuffer,
    object_store: JobArtifactObjectStore | None = None,
) -> AgentPlane:
    bundle_dir = settings.data_dir / "agent_bundles"
    broker = AgentExecutionBroker(
        job_db.path,
        lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
        bundle_dir=bundle_dir,
        data_dir=settings.data_dir,
        agent_status=agent_manager,
        is_workspace_paused=workspace_worker_control.is_paused,
        job_db=job_db,
        job_event_buffer=job_event_buffer,
    )
    dispatch = AgentDispatchService(settings, broker, artifact_store)
    skill_manager = build_skill_manager(settings.database_url, settings.skills_runs_dir)
    executor_leases = ExecutorLeaseRepository(
        job_db,
        data_dir=settings.data_dir,
        job_event_manager=job_event_manager,
        job_event_buffer=job_event_buffer,
    )
    worker_registry = AgentWorkerRegistry(job_db.path)
    completion = AgentCompletionHandler(
        executor_leases,
        artifact_store,
        settings.jobs_dir,
        bundle_dir,
        skill_manager=skill_manager,
        object_store=object_store,
        max_archive_bytes=settings.executor_runtime.agent_workers.max_archive_bytes,
    )
    return AgentPlane(
        broker=broker,
        dispatch=dispatch,
        worker_registry=worker_registry,
        completion=completion,
        executor_leases=executor_leases,
    )
