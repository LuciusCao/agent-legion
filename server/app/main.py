import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.notifications import NotificationHub
from server.app.events import VideoEventManager
from server.app.pipeline.recovery import recover_interrupted_videos
from server.app.pipeline.runners import RunnerPool
from server.app.routes import create_router
from server.app.settings import load_settings
from server.app.worker_thread import WorkerThread


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
    max_workers: int | None = None,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)

    agent_manager = AgentStatusManager()
    video_event_manager = VideoEventManager()
    hub = NotificationHub()
    hub.on_change = video_event_manager.broadcast
    hub.on_delete = video_event_manager.broadcast_delete
    hub.on_detail_change = video_event_manager.broadcast_video_detail
    db = Database(settings.data_dir / "video_hive.sqlite", hub=hub)

    worker_thread: WorkerThread | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_thread
        video_event_manager._loop = asyncio.get_running_loop()
        if start_worker:
            agent_manager.discover()
            recover_interrupted_videos(db, settings)
            runner_pool = RunnerPool.from_settings(
                settings, [a.id for a in agent_manager.agents]
            )
            runner_counts: dict[str, int] = {}
            for runner in runner_pool.all_runners():
                aid = runner.agent_id
                if aid:
                    runner_counts[aid] = runner_counts.get(aid, 0) + 1
            agent_manager.set_runner_counts(runner_counts)
            worker_thread = WorkerThread(db, settings, runner_pool, agent_manager, max_workers)
            worker_thread.start()
        yield
        if worker_thread is not None:
            worker_thread.stop()

    app = FastAPI(title="Video Hive", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.agent_manager = agent_manager
    app.state.video_event_manager = video_event_manager

    app.include_router(create_router(db, settings, agent_manager, video_event_manager))

    frontend_dist = settings.root_dir / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

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
