import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.app.api import create_router
from server.app.db import Database
from server.app.settings import load_settings
from server.app.worker import process_next


def create_app(data_dir: Path | None = None, start_worker: bool = False) -> FastAPI:
    settings = load_settings(data_dir=data_dir)
    db = Database(settings.data_dir / "video_hive.sqlite")
    stop_event = threading.Event()

    def worker_loop() -> None:
        while not stop_event.is_set():
            processed = process_next(db, settings)
            stop_event.wait(1 if processed else 3)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = None
        if start_worker:
            thread = threading.Thread(target=worker_loop, name="video-hive-worker", daemon=True)
            thread.start()
        yield
        stop_event.set()
        if thread:
            thread.join(timeout=3)

    app = FastAPI(title="Video Hive", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.include_router(create_router(db, settings))

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
