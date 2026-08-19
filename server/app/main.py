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
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.sweeper import SweeperThread
from server.app.http_middleware import add_http_middleware
from server.app.jobs import JobQueries
from server.app.openclaw_agents import list_openclaw_agents
from server.app.routes import create_router
from server.app.routes.auth import create_auth_router
from server.app.scheduler_wakeup import unregister_wakeup
from server.app.services.artifact_orphan_gc import ArtifactOrphanGcThread
from server.app.services.artifact_store import ArtifactStore
from server.app.services.demo_node_seed import seed_demo_node_codes
from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.instance_settings import apply_instance_settings
from server.app.services.job_intake_queue import JobIntakeQueue
from server.app.services.job_packages import JobPackageService
from server.app.services.ops_metrics import OpsMetricsService
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_replays import QualityReplayService
from server.app.services.quality_sampling import QualitySamplingService
from server.app.services.quality_stats import QualityStatsService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import load_settings, validate_settings
from server.app.skills.runtime import build_skill_manager
from server.app.skills.seed import seed_skill_sources
from server.app.spa import mount_spa
from server.app.startup_tasks import BackgroundTasks
from server.app.studio_chat.service import StudioChatService
from server.app.worker_control import WorkspaceWorkerControl
from server.app.worker_startup import start_worker_threads
from server.app.workflow_worker.thread import WorkflowWorkerThread


def create_app(data_dir: Path | None = None, start_worker: bool = False) -> FastAPI:
    settings = load_settings(data_dir=data_dir)
    event_bus = InProcessEventBus()
    agent_manager = AgentStatusManager(
        event_bus=event_bus, discover_agents=lambda: list_openclaw_agents(timeout=10)
    )
    job_event_manager = JobEventManager(event_bus)
    job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
    # Hydrate instance-level settings from the DB before any service reads
    # them (executor runtime, cleanup/monitoring config).
    apply_instance_settings(settings, job_db.path)
    # Executor definitions are retired (schema v47, P-0.5); only the demo
    # workflow's global node_code versions still seed from the git-reviewed
    # workflow_nodes/ sources (#96).
    seed_demo_node_codes(settings)
    # Agent definitions are workspace-scoped (schema v46): there is no global
    # seed. Workspaces initialized from the sample template get the factory
    # agent templates instantiated seed-if-absent at creation time
    # (WorkflowRevisionService.ensure_active_revision). The workflow catalog
    # is retired (schema v50, #112): a workflow is the DAG inside one
    # workspace, keyed by workspaces.default_workflow_key as plain text.
    # Skill sources/lock retired from tracked yaml into global_settings:
    # import-once the legacy files when present, else seed the built-in
    # constants; with rows present this is a no-op (DB is authoritative).
    seed_skill_sources(settings.database_url, settings.root_dir)
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
        data_dir=settings.data_dir,
        agent_status=agent_manager,
        is_workspace_paused=workspace_worker_control.is_paused,
        job_db=job_db,
        job_event_buffer=job_event_buffer,
    )
    agent_dispatch = AgentDispatchService(settings, agent_broker, artifact_store)
    skill_manager = build_skill_manager(settings.database_url)

    executor_leases = ExecutorLeaseRepository(
        job_db.path,
        job_db=job_db,
        data_dir=settings.data_dir,
        job_event_manager=job_event_manager,
        job_event_buffer=job_event_buffer,
    )
    agent_worker_registry = AgentWorkerRegistry(job_db.path)
    ops_metrics = OpsMetricsService(job_db.path, settings.config)
    # Studio chat (phase 3 chunk 4): ACP conversation sessions, one agent
    # subprocess per session; in-process only, reaped in the lifespan finally.
    studio_chat_service = StudioChatService(job_db, settings, job_event_manager.bus)
    # PATH-level availability of every registered chat agent: warms the probe
    # cache and logs the entries the picker will hide on this host.
    studio_chat_service.warm_availability_probe()
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
                agent_broker=agent_broker,
                workspace_worker_control=workspace_worker_control,
                agent_manager=agent_manager,
                agent_dispatch=agent_dispatch,
            )
            app.state.worker_startup = worker_status
            # Routes pick the thread up here to trigger scan-list reloads
            # (workflow registration hot refresh).
            app.state.workflow_worker = workflow_worker_thread
            if workflow_worker_thread is not None:
                app.state.code_executor = workflow_worker_thread.runtime.executor
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
            # Reap chat sessions before closing DB pools: teardown revokes
            # scoped tokens and settles permission waiters via the DB.
            studio_chat_service.shutdown()
            for thread in (sweeper_thread, artifact_gc_thread):
                if thread is not None:
                    thread.stop()
            if workflow_worker_thread is not None:
                unregister_wakeup(workflow_worker_thread.wake)
                workflow_worker_thread.stop()
            close_database_pools()

    app = FastAPI(title="Agent Legion", lifespan=lifespan)
    add_http_middleware(app, settings)
    app.state.settings = settings
    app.state.job_db = job_db
    app.state.auth_service = build_auth_service(job_db, settings.config)
    app.state.agent_broker = agent_broker
    app.state.agent_dispatch = agent_dispatch
    app.state.agent_worker_registry = agent_worker_registry
    app.state.agent_completion = agent_completion
    app.state.executor_leases = executor_leases
    app.state.artifact_store = artifact_store
    app.state.agent_manager = agent_manager
    app.state.workspace_worker_control = workspace_worker_control
    app.state.job_event_manager = job_event_manager
    app.state.studio_chat_service = studio_chat_service
    app.state.event_bus = job_event_manager.bus
    app.state.job_event_buffer = job_event_buffer
    app.state.workspace_event_aggregator = workspace_event_aggregator
    executor_catalog = ExecutorCatalogService(settings)
    workspace_executor_configuration = WorkspaceExecutorConfigurationService(job_db, settings)
    workspace_configuration = WorkspaceConfigurationService(job_db, settings, agent_manager)
    job_packages = JobPackageService(job_db, settings)
    app.include_router(create_auth_router(app.state.auth_service), prefix="/api")
    app.include_router(
        create_router(
            job_db,
            settings,
            agent_manager,
            workspace_worker_control,
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
            quality_sampling=QualitySamplingService(job_db.path),
            quality_labels=QualityLabelService(job_db.path, artifact_store),
            quality_stats=QualityStatsService(job_db.path),
            quality_replays=QualityReplayService(job_db, artifact_store),
            studio_chat_service=studio_chat_service,
        )
    )
    mount_spa(app, settings.root_dir / "frontend" / "dist")
    return app


if os.environ.get("AGENT_LEGION_SKIP_MODULE_APP") == "1":
    app = FastAPI()
else:
    app = create_app(start_worker=True)
