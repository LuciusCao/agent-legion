import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.migrations.report import MigrationBlockedError
from server.app.db.notifications import NotificationHub
from server.app.events import JobEventManager, VideoEventManager
from server.app.executors.backup import legacy_backup_path
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.executors.runtime_factory import build_execution_runtime
from server.app.job_events import build_workspace_event_aggregator
from server.app.jobs import JobQueries
from server.app.local_handler_loader import build_local_handlers
from server.app.routes import create_router
from server.app.services.workspace_pi_agents import sync_workspace_pi_agents
from server.app.settings import Settings, load_settings, validate_settings
from server.app.skills.manager import SkillManager
from server.app.spa import mount_spa
from server.app.startup_tasks import BackgroundTasks
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.registry import list_registered_workflows


def build_executor_registry(
    settings: Settings,
    job_db: Any | None = None,
) -> ExecutorRegistry:
    """Build the application-wide executor registry from settings.

    The registry is constructed once per application lifecycle and reused across
    all workspaces. Runtime dependencies (Pi binary, OpenClaw template, local
    handlers) are injected here so adapters remain environment-agnostic.
    """
    skill_manager = SkillManager(
        config_path=settings.root_dir / "config" / "skills.yaml",
        lock_path=settings.root_dir / "config" / "skills.lock",
        base_dir=Path.home() / ".agents" / "skills" / "agent-legion",
    )
    runtime = RuntimeDependencies(
        local_handlers=build_local_handlers(settings),
        pi_runtime=settings.executor_runtime.workflows.pi,
        skill_manager=skill_manager,
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)

    agent_manager = AgentStatusManager()
    workspace_worker_control = WorkspaceWorkerControl()
    video_event_manager = VideoEventManager()
    job_event_manager = JobEventManager()
    hub = NotificationHub()
    hub.on_change = video_event_manager.broadcast  # type: ignore[assignment]
    hub.on_delete = video_event_manager.broadcast_delete
    hub.on_detail_change = video_event_manager.broadcast_video_detail  # type: ignore[assignment]
    db = Database(settings.data_dir / "video_hive.sqlite", hub=hub, videos_dir=settings.videos_dir)
    job_db = JobQueries(settings.data_dir / "video_hive.sqlite", jobs_dir=settings.jobs_dir)
    executor_registry = build_executor_registry(settings, job_db)

    job_event_buffer, workspace_event_aggregator = build_workspace_event_aggregator(
        job_db, settings, job_event_manager
    )
    definitions = list_registered_workflows(settings.root_dir)
    with job_db.connect() as conn:
        try:
            finalize_legacy_executor_schema(
                conn,
                definitions,
                settings.executor_definitions,
                backup_path=legacy_backup_path(job_db.path),
            )
        except MigrationBlockedError as exc:
            check_cmd = (
                "UV_CACHE_DIR=.uv-cache uv run python "
                "scripts/finalize-workspace-executor-migration.py --check"
            )
            logging.getLogger(__name__).error(
                "Workspace executor finalization blocked:\n%s\n\nRun: %s",
                exc.report.to_json(),
                check_cmd,
            )
            raise RuntimeError(
                f"Workspace executor finalization blocked: {exc.report.to_json()}. "
                f"Run `{check_cmd}` for details."
            ) from exc
    workflow_worker_thread: WorkflowWorkerThread | None = None
    background_tasks = BackgroundTasks(
        workspace_event_aggregator=workspace_event_aggregator,
        agent_broadcast_controller=agent_manager._broadcast_controller,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal workflow_worker_thread
        video_event_manager._loop = asyncio.get_running_loop()
        job_event_manager._loop = asyncio.get_running_loop()
        if start_worker:
            validate_settings(settings)
            agent_manager.discover()
            sync_workspace_pi_agents(job_db, settings, agent_manager)
            if WorkflowWorkerThread.is_enabled(settings):
                executor_leases = ExecutorLeaseRepository(
                    job_db.path,
                    job_db=job_db,
                    job_event_manager=job_event_manager,
                    job_event_buffer=job_event_buffer,
                )
                execution_runtime = build_execution_runtime(
                    executor_leases, executor_registry, settings.executor_runtime
                )
                workflow_worker_thread = WorkflowWorkerThread(
                    job_db=job_db,
                    leases=executor_leases,
                    registry=executor_registry,
                    runtime=execution_runtime,
                    settings=settings,
                    workspace_worker_control=workspace_worker_control,
                    agent_manager=agent_manager,
                )
                try:
                    workflow_worker_thread.start()
                except Exception:
                    logging.getLogger(__name__).exception("workflow worker failed to start")
        background_tasks.start(app)
        yield
        background_tasks.stop(app)
        if workflow_worker_thread is not None:
            workflow_worker_thread.stop()

    app = FastAPI(title="Agent Legion", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors.allow_origins),
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.db = db
    app.state.job_db = job_db
    app.state.executor_registry = executor_registry
    app.state.agent_manager = agent_manager
    app.state.workspace_worker_control = workspace_worker_control
    app.state.video_event_manager = video_event_manager
    app.state.job_event_manager = job_event_manager
    app.state.job_event_buffer = job_event_buffer
    app.state.workspace_event_aggregator = workspace_event_aggregator
    app.include_router(
        create_router(
            db,
            job_db,
            settings,
            agent_manager,
            video_event_manager,
            workspace_worker_control,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        )
    )
    mount_spa(app, settings.root_dir / "frontend" / "dist")
    return app


app = create_app(start_worker=True)
