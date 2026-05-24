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
from server.app.events import VideoEventManager
from server.app.pipeline.recovery import recover_interrupted_videos
from server.app.pipeline.runners import RunnerPool
from server.app.settings import load_settings
from server.app.worker import (
    DEFAULT_PHASE_CONCURRENCY,
    WorkerCapacity,
    get_phase_concurrency_limit,
    pick_next_work,
    process_video_once,
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
    running_local_counts: dict[str, int] = {}
    running_lock = threading.Lock()

    agent_manager = AgentStatusManager()
    video_event_manager = VideoEventManager()
    runner_pool: RunnerPool | None = None

    def worker_loop() -> None:
        while not stop_event.is_set():
            submitted = False
            runner_slot = None
            if runner_pool is not None:
                try:
                    runner_slot = runner_pool.acquire()
                except RuntimeError:
                    runner_slot = None
            with running_lock:
                running_video_ids = set(running_futures)
                local_counts = dict(running_local_counts)
            work = pick_next_work(
                db.list_videos(),
                running_video_ids=running_video_ids,
                capacity=WorkerCapacity(
                    free_runner=runner_slot,
                    running_local_counts=local_counts,
                ),
                settings=settings,
            )
            if work is None:
                if runner_slot is not None and runner_pool is not None:
                    runner_pool.release(runner_slot[0])
                stop_event.wait(3)
                continue

            video = work.video
            if work.kind == "agent":
                if runner_slot is None:
                    stop_event.wait(1)
                    continue
                runner_index, runner = runner_slot
                agent_id = getattr(runner, "agent_id", f"runner-{runner_index}")
                agent_manager.set_busy(agent_id, video)
                future = executor.submit(
                    process_video_once,
                    db,
                    settings,
                    video["id"],
                    None,
                    runner,
                )
                with running_lock:
                    running_futures[video["id"]] = future
                future.add_done_callback(
                    lambda _f, vid=video["id"], idx=runner_index, aid=agent_id: finish_agent_work(
                        vid, idx, aid
                    )
                )
                submitted = True
            else:
                if runner_slot is not None and runner_pool is not None:
                    runner_pool.release(runner_slot[0])
                future = executor.submit(process_video_once, db, settings, video["id"])
                with running_lock:
                    running_futures[video["id"]] = future
                    running_local_counts[work.phase] = running_local_counts.get(work.phase, 0) + 1
                future.add_done_callback(
                    lambda _f, vid=video["id"], phase=work.phase: finish_local_work(vid, phase)
                )
                submitted = True
            stop_event.wait(1 if submitted else 3)

    def finish_agent_work(video_id: str, runner_index: int, agent_id: str) -> None:
        with running_lock:
            running_futures.pop(video_id, None)
        if runner_pool is not None:
            runner_pool.release(runner_index)
        agent_manager.set_idle(agent_id)

    def finish_local_work(video_id: str, phase: str) -> None:
        with running_lock:
            running_futures.pop(video_id, None)
            next_count = running_local_counts.get(phase, 0) - 1
            if next_count > 0:
                running_local_counts[phase] = next_count
            else:
                running_local_counts.pop(phase, None)

    def configured_worker_count(runner_count: int) -> int:
        if max_workers is not None:
            return max_workers
        local_slots = sum(
            get_phase_concurrency_limit(settings, phase)
            for phase in DEFAULT_PHASE_CONCURRENCY
        )
        return max(1, runner_count + local_slots)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal executor, runner_pool
        thread = None
        if start_worker:
            agent_manager.discover()
            recover_interrupted_videos(db, settings)
            runner_pool = RunnerPool.from_settings(
                settings, [a.id for a in agent_manager.agents]
            )
            for i, runner in enumerate(runner_pool.all_runners()):
                runner.agent_id = (
                    agent_manager.agents[i].id
                    if i < len(agent_manager.agents)
                    else f"runner-{i}"
                )
            runner_count = runner_pool.size()
            workers = configured_worker_count(runner_count)
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
    app.state.video_event_manager = video_event_manager

    db._on_change = video_event_manager.broadcast
    db._on_delete = video_event_manager.broadcast_delete

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
