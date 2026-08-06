import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from server.app.agent_broker import AgentDispatchService, AgentExecutionBroker
from server.app.agent_completion import AgentCompletionHandler
from server.app.agent_workers import AgentWorkerRegistry
from server.app.auth.service import build_auth_service
from server.app.db.connection import close_database_pools
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.aggregator import build_workspace_event_aggregator
from server.app.events.bus import InProcessEventBus
from server.app.executors.executor_registry_factory import build_executor_registry
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.pi import build_skill_manager
from server.app.executors.sweeper import SweeperThread
from server.app.http_middleware import add_http_middleware
from server.app.jobs import JobQueries
from server.app.pipeline.runners import list_openclaw_agents
from server.app.routes import create_router
from server.app.routes.auth import create_auth_router
from server.app.scheduler_wakeup import unregister_wakeup
from server.app.services.artifact_orphan_gc import ArtifactOrphanGcThread
from server.app.services.artifact_store import ArtifactStore
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_intake_queue import JobIntakeQueue
from server.app.services.job_packages import JobPackageService
from server.app.services.ops_metrics import OpsMetricsService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import load_settings, validate_settings
from server.app.spa import mount_spa
from server.app.startup_tasks import BackgroundTasks
from server.app.worker_control import WorkspaceWorkerControl
from server.app.worker_startup import start_worker_threads
from server.app.workflow_worker.thread import WorkflowWorkerThread


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)
    event_bus = InProcessEventBus()
    agent_manager = AgentStatusManager(
        event_bus=event_bus,
        discover_agents=lambda: list_openclaw_agents(timeout=10),
    )
    job_event_manager = JobEventManager(event_bus)
    job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
    WorkflowRevisionService(job_db).reconcile_active_agent_routes()
    workspace_worker_control = WorkspaceWorkerControl(db_path=job_db.path)
    # Resume state must not survive a restart: dispatch stays off until an
    # operator explicitly resumes it in this process lifetime.
    workspace_worker_control.reset_all_to_paused()
    artifact_store = ArtifactStore(settings.data_dir / "artifacts", job_db.path)
    job_event_buffer, workspace_event_aggregator = build_workspace_event_aggregator(
        job_db, settings, job_event_manager.bus
    )
    agent_broker = AgentExecutionBroker(
        job_db.path,
        lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
        bundle_dir=settings.data_dir / "agent_bundles",
        agent_status=agent_manager,
        is_workspace_paused=workspace_worker_control.is_paused,
        job_db=job_db,
        job_event_buffer=job_event_buffer,
    )
    agent_dispatch = AgentDispatchService(settings, agent_broker, artifact_store)
    skill_manager = build_skill_manager(settings.root_dir)
    executor_registry = build_executor_registry(
        settings, job_db, artifact_store=artifact_store, skill_manager=skill_manager
    )

    executor_leases = ExecutorLeaseRepository(
        job_db.path,
        job_db=job_db,
        data_dir=settings.data_dir,
        job_event_manager=job_event_manager,
        job_event_buffer=job_event_buffer,
    )
    agent_worker_registry = AgentWorkerRegistry(job_db.path)
    ops_metrics = OpsMetricsService(job_db.path, settings.config)
    agent_completion = AgentCompletionHandler(
        executor_leases,
        artifact_store,
        settings.jobs_dir,
        settings.data_dir / "agent_bundles",
        skill_manager=skill_manager,
    )
    workflow_worker_thread: WorkflowWorkerThread | None = None
    sweeper_thread: SweeperThread | None = None
    artifact_gc_thread: ArtifactOrphanGcThread | None = None
    background_tasks = BackgroundTasks(
        workspace_event_aggregator=workspace_event_aggregator,
        agent_broadcast_controller=agent_manager.broadcast_controller,
        job_intake_queue=JobIntakeQueue(job_db, settings, job_event_buffer),
        ops_metrics=ops_metrics,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal workflow_worker_thread, sweeper_thread, artifact_gc_thread
        job_event_manager.bus.attach_loop(asyncio.get_running_loop())
        if start_worker:
            validate_settings(settings)
            agent_manager.discover()
            sweeper_thread, workflow_worker_thread, worker_status = start_worker_threads(
                settings,
                job_db=job_db,
                executor_leases=executor_leases,
                executor_registry=executor_registry,
                agent_broker=agent_broker,
                workspace_worker_control=workspace_worker_control,
                agent_manager=agent_manager,
                agent_dispatch=agent_dispatch,
            )
            app.state.worker_startup = worker_status
            # Orphan GC shares the sweeper ownership rule: exactly one
            # replica (sweeper_enabled) reclaims, the rest stay idle.
            if settings.executor_runtime.sweeper_enabled:
                artifact_gc_thread = ArtifactOrphanGcThread(artifact_store)
                artifact_gc_thread.start()
        background_tasks.start(app)
        try:
            yield
        finally:
            await background_tasks.stop(app)
            if sweeper_thread is not None:
                sweeper_thread.stop()
            if artifact_gc_thread is not None:
                artifact_gc_thread.stop()
            if workflow_worker_thread is not None:
                unregister_wakeup(workflow_worker_thread.wake)
                workflow_worker_thread.stop()
            close_database_pools()

    app = FastAPI(title="Agent Legion", lifespan=lifespan)
    add_http_middleware(app, settings)
    app.state.settings = settings
    app.state.job_db = job_db
    app.state.auth_service = build_auth_service(job_db, settings.config)
    app.state.executor_registry = executor_registry
    app.state.agent_broker = agent_broker
    app.state.agent_dispatch = agent_dispatch
    app.state.agent_worker_registry = agent_worker_registry
    app.state.agent_completion = agent_completion
    app.state.executor_leases = executor_leases
    app.state.artifact_store = artifact_store
    app.state.agent_manager = agent_manager
    app.state.workspace_worker_control = workspace_worker_control
    app.state.job_event_manager = job_event_manager
    app.state.event_bus = job_event_manager.bus
    app.state.job_event_buffer = job_event_buffer
    app.state.workspace_event_aggregator = workspace_event_aggregator
    workflow_catalog = WorkflowCatalogService(settings)
    executor_catalog = ExecutorCatalogService(settings)
    workspace_executor_configuration = WorkspaceExecutorConfigurationService(
        job_db, settings.executor_definitions
    )
    workspace_configuration = WorkspaceConfigurationService(
        job_db, settings, agent_manager, workflow_catalog
    )
    job_packages = JobPackageService(job_db, settings)
    app.include_router(create_auth_router(app.state.auth_service), prefix="/api")
    app.include_router(
        create_router(
            job_db,
            settings,
            agent_manager,
            workspace_worker_control,
            workflow_catalog=workflow_catalog,
            executor_catalog=executor_catalog,
            workspace_executor_configuration=workspace_executor_configuration,
            workspace_configuration=workspace_configuration,
            job_packages=job_packages,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
            artifact_store=artifact_store,
            agent_broker=agent_broker,
            agent_worker_registry=agent_worker_registry,
            agent_completion=agent_completion,
            ops_metrics=ops_metrics,
        )
    )
    mount_spa(app, settings.root_dir / "frontend" / "dist")
    return app


if os.environ.get("AGENT_LEGION_SKIP_MODULE_APP") == "1":
    app = FastAPI()
else:
    app = create_app(start_worker=True)
