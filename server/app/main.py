import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.app.agents import AgentStatusManager
from server.app.api import create_router
from server.app.db import Database
from server.app.settings import load_settings
from server.app.worker import (
    acquire_runner,
    init_runners,
    process_video_once,
    recover_interrupted_videos,
    release_runner,
)


def create_app(
    data_dir: Path | None = None,
    start_worker: bool = False,
    max_workers: int | None = None,
) -> FastAPI:
    settings = load_settings(data_dir=data_dir)
    db = Database(settings.data_dir / "video_hive.sqlite")
    stop_event = threading.Event()

    executor: ThreadPoolExecutor | None = None
    running_futures: dict[str, Future[bool]] = {}

    agent_manager = AgentStatusManager()

    def worker_loop() -> None:
        while not stop_event.is_set():
            submitted = False
            try:
                runner_index, runner = acquire_runner()
            except RuntimeError:
                stop_event.wait(1)
                continue
            agent_id = getattr(runner, "agent_id", f"runner-{runner_index}")
            for video in db.list_videos():
                if video["status"] not in {"queued", "missing_url"}:
                    continue
                if video["id"] in running_futures:
                    continue
                agent_manager.set_busy(agent_id, video["id"])
                future = executor.submit(
                    process_video_once,
                    db,
                    settings,
                    video["id"],
                    None,
                    runner,
                )
                running_futures[video["id"]] = future
                future.add_done_callback(
                    lambda _f, vid=video["id"], idx=runner_index, aid=agent_id: (
                        running_futures.pop(vid, None),
                        release_runner(idx),
                        agent_manager.set_idle(aid),
                    )
                )
                submitted = True
                break
            else:
                release_runner(runner_index)
            stop_event.wait(1 if submitted else 3)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal executor
        thread = None
        if start_worker:
            agent_manager.discover()
            recover_interrupted_videos(db, settings)
            runner_count = init_runners(settings, agent_manager)
            workers = max_workers if max_workers is not None else max(1, runner_count)
            executor = ThreadPoolExecutor(max_workers=workers)
            thread = threading.Thread(target=worker_loop, name="video-hive-worker", daemon=True)
            thread.start()
        yield
        stop_event.set()
        if thread:
            thread.join(timeout=3)
        if executor:
            executor.shutdown(wait=False)

    app = FastAPI(title="Video Hive", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.agent_manager = agent_manager
    app.include_router(create_router(db, settings, agent_manager))

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
