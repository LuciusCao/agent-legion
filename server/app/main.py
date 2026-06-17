import asyncio
import importlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.migrations.report import MigrationBlockedError
from server.app.db.notifications import NotificationHub
from server.app.events import JobEventManager, VideoEventManager
from server.app.executors.backup import legacy_backup_path
from server.app.executors.config import LocalExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.executors.local import LocalHandler
from server.app.executors.registry import ExecutorRegistry, RuntimeDependencies
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs import JobQueries
from server.app.pipeline.recovery import recover_interrupted_videos
from server.app.pipeline.runners import RunnerPool
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.registry import list_registered_pipelines
from server.app.routes import create_router
from server.app.services.workspace_pi_agents import sync_workspace_pi_agents
from server.app.settings import Settings, load_settings, validate_settings
from server.app.worker_control import WorkerControl, WorkspaceWorkerControl
from server.app.worker_thread import WorkerThread


def _build_local_handlers(settings: Settings) -> dict[str, LocalHandler]:
    """Resolve local handler references from executor definitions into callables."""
    handlers: dict[str, LocalHandler] = {}
    for config in settings.executor_definitions.values():
        if not isinstance(config, LocalExecutorConfig):
            continue
        for capability_config in config.capabilities.values():
            handler_key = capability_config.handler
            if handler_key in handlers or "." not in handler_key:
                continue
            module_name, func_name = handler_key.rsplit(".", 1)
            full_module_name = f"server.app.pipelines.{module_name}"
            try:
                module = importlib.import_module(full_module_name)
                func = getattr(module, func_name)
                if callable(func):
                    handlers[handler_key] = func
                else:
                    logging.getLogger(__name__).warning(
                        "Local handler %s is not callable", handler_key
                    )
            except Exception:
                logging.getLogger(__name__).warning("Could not load local handler %s", handler_key)
    return handlers


def build_executor_registry(
    settings: Settings,
    job_db: Any | None = None,
) -> ExecutorRegistry:
    """Build the application-wide executor registry from settings.

    The registry is constructed once per application lifecycle and reused across
    all workspaces. Runtime dependencies (Pi binary, OpenClaw template, local
    handlers) are injected here so adapters remain environment-agnostic.
    """
    runtime = RuntimeDependencies(
        local_handlers=_build_local_handlers(settings),
        pi_runtime=settings.executor_runtime.pipelines.pi,
        pi_skill_root=settings.root_dir / "server" / "app" / "pipelines" / "skills",
        openclaw_runtime=settings.executor_runtime.openclaw,
        settings_config=settings.config,
        job_db=job_db,
        cancellation_grace_seconds=settings.executor_runtime.cancellation_grace_seconds,
    )
    return ExecutorRegistry.build(settings.executor_definitions, runtime)


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
    max_workers: int | None = None,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)

    agent_manager = AgentStatusManager()
    worker_control = WorkerControl()
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

    definitions = list_registered_pipelines(settings.root_dir)
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

    worker_thread: WorkerThread | None = None
    pipeline_worker_thread: PipelineWorkerThread | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal pipeline_worker_thread, worker_thread
        video_event_manager._loop = asyncio.get_running_loop()
        job_event_manager._loop = asyncio.get_running_loop()
        if start_worker:
            validate_settings(settings)
            agent_manager.discover()
            sync_workspace_pi_agents(job_db, settings, agent_manager)
            recover_interrupted_videos(db, settings)
            runner_pool = RunnerPool.from_settings(
                settings, [a.id for a in agent_manager.agents], agent_manager=agent_manager
            )
            runner_counts: dict[str, int] = {}
            for runner in runner_pool.all_runners():
                aid = runner.agent_id
                if aid:
                    runner_counts[aid] = runner_counts.get(aid, 0) + 1
            agent_manager.set_runner_counts(runner_counts)
            worker_thread = WorkerThread(
                db, settings, runner_pool, agent_manager, worker_control, max_workers
            )
            worker_thread.start()
            if PipelineWorkerThread.is_enabled(settings):
                executor_leases = ExecutorLeaseRepository(
                    job_db.path, job_db=job_db, job_event_manager=job_event_manager
                )
                execution_runtime = ExecutionRuntime(executor_leases, executor_registry)
                pipeline_worker_thread = PipelineWorkerThread(
                    job_db=job_db,
                    leases=executor_leases,
                    registry=executor_registry,
                    runtime=execution_runtime,
                    settings=settings,
                    workspace_worker_control=workspace_worker_control,
                    agent_manager=agent_manager,
                )
                try:
                    pipeline_worker_thread.start()
                except Exception:
                    logging.getLogger(__name__).exception("pipeline worker failed to start")
        yield
        if pipeline_worker_thread is not None:
            pipeline_worker_thread.stop()
        if worker_thread is not None:
            worker_thread.stop()

    app = FastAPI(title="Video Hive", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.job_db = job_db
    app.state.executor_registry = executor_registry
    app.state.agent_manager = agent_manager
    app.state.worker_control = worker_control
    app.state.workspace_worker_control = workspace_worker_control
    app.state.video_event_manager = video_event_manager
    app.state.job_event_manager = job_event_manager

    app.include_router(
        create_router(
            db,
            job_db,
            settings,
            agent_manager,
            video_event_manager,
            worker_control,
            workspace_worker_control,
            job_event_manager=job_event_manager,
        )
    )

    frontend_dist = settings.root_dir / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_index = frontend_dist / "index.html"
    if frontend_index.exists() and frontend_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            requested = frontend_dist / path
            if path and requested.exists() and requested.is_file():
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")

    else:

        @app.get("/", response_class=HTMLResponse)
        def frontend_missing() -> str:
            return (
                "<main style='font-family: system-ui; padding: 24px'>"
                "<h1>Video Hive API</h1>"
                "<p>Run the TypeScript frontend with <code>cd frontend && npm run dev</code>.</p>"
                "</main>"
            )

    return app


app = create_app(start_worker=True)
