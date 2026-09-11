"""Agent execution plane assembly (issue #190).

One builder for the objects that execute agent-routed nodes end to end:
the broker (queue + leases), the dispatch service (bundle production), the
worker registry (registration/auth), and the completion handler (result
commit). Extracted from ``main.py`` so the composition root reads as domain
groups instead of 25 inline constructors.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from server.app.agent_broker import AgentDispatchService, AgentExecutionBroker
from server.app.agent_broker.result_commit_batcher import ResultCommitBatcher
from server.app.agent_control import AgentCompletionHandler, AgentWorkerRegistry
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.buffer import JobEventBuffer
from server.app.executors._lease_finish_batch import (
    finish_many_with_retry as _finish_many_with_retry,
)
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
    # #591 group-commit writer; the lifespan starts/stops it.
    result_commit_batcher: ResultCommitBatcher | None = None


def build_agent_plane(
    job_db: JobQueries,
    settings: Settings,
    agent_manager: AgentStatusManager,
    workspace_worker_control: WorkspaceWorkerControl,
    artifact_store: ArtifactStore,
    job_event_manager: JobEventManager,
    job_event_buffer: JobEventBuffer,
    object_store: JobArtifactObjectStore | None = None,
    *,
    # #591 C1: gate on the flag that starts the writer — a start_worker=False
    # plane must carry no batcher (its submit would park on an undrained
    # future).
    result_batching: bool = True,
) -> AgentPlane:
    bundle_dir = settings.data_dir / "agent_bundles"
    # #591: the batcher precedes its two owners (both receive it); the
    # late-bound arms are the retry-wrapped batch module and the broker's
    # mark_done_many. The writer starts only in the app lifespan.
    batcher = (
        ResultCommitBatcher(None, None)
        if result_batching and settings.executor_runtime.agent_workers.result_commit_batching
        else None
    )
    executor_leases = ExecutorLeaseRepository(
        job_db,
        data_dir=settings.data_dir,
        job_event_manager=job_event_manager,
        job_event_buffer=job_event_buffer,
        result_batcher=batcher,
    )
    broker = AgentExecutionBroker(
        job_db,
        lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
        bundle_dir=bundle_dir,
        data_dir=settings.data_dir,
        agent_status=agent_manager,
        is_workspace_paused=workspace_worker_control.is_paused,
        job_db=job_db,
        job_event_buffer=job_event_buffer,
        touch_worker_interval_seconds=(
            settings.executor_runtime.agent_claim.worker_touch_interval_seconds
        ),
        result_batcher=batcher,
    )
    if batcher is not None:
        batcher.finish_many = partial(_finish_many_with_retry, executor_leases)
        batcher.mark_done_many = broker.mark_done_many
    dispatch = AgentDispatchService(settings, broker, artifact_store)
    skill_manager = build_skill_manager(job_db, settings.skills_runs_dir)
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
        worker_registry=AgentWorkerRegistry(job_db),
        completion=completion,
        executor_leases=executor_leases,
        result_commit_batcher=batcher,
    )
