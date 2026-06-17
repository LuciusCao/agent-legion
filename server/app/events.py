import asyncio
import json
import logging
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


class VideoEventManager:
    """Manages Server-Sent Events (SSE) connections for video status updates."""

    # Cap concurrent SSE connections to prevent memory/CPU leaks from reconnect storms
    MAX_CLIENTS = 100

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._video_clients: dict[str, set[asyncio.Queue[str]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, request: Request) -> StreamingResponse:
        # Evict oldest client when at capacity
        if len(self._clients) >= self.MAX_CLIENTS:
            oldest = next(iter(self._clients))
            self._clients.discard(oldest)

        # Bounded queue: prevents unbounded memory growth for dead connections
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._clients.add(queue)

        async def event_stream():
            try:
                while True:
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"data: {data}\n\n"
                    except TimeoutError:
                        yield ":heartbeat\n\n"
            except asyncio.CancelledError:
                # StreamingResponse 检测到客户端断开后会取消 generator
                raise
            finally:
                self._clients.discard(queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    async def connect_video(self, request: Request, video_id: str) -> StreamingResponse:
        if len(self._clients) >= self.MAX_CLIENTS:
            oldest = next(iter(self._clients))
            self._clients.discard(oldest)

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._clients.add(queue)
        self._video_clients.setdefault(video_id, set()).add(queue)

        async def event_stream():
            try:
                while True:
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"data: {data}\n\n"
                    except TimeoutError:
                        yield ":heartbeat\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                self._clients.discard(queue)
                self._video_clients.get(video_id, set()).discard(queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def _broadcast(self, payload: str) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        def _send() -> None:
            """在 event loop 线程中同步发送，避免 run_coroutine_threadsafe 的 Future 开销。"""
            dead: set[asyncio.Queue[str]] = set()
            for queue in list(self._clients):
                try:
                    queue.put_nowait(payload)
                except Exception:
                    # QueueFull 或任何异常都视为死连接
                    dead.add(queue)
            if dead:
                self._clients -= dead
                for vid, queues in list(self._video_clients.items()):
                    cleaned = queues - dead
                    if cleaned:
                        self._video_clients[vid] = cleaned
                    else:
                        self._video_clients.pop(vid, None)

        loop.call_soon_threadsafe(_send)

    def _broadcast_to_video(self, video_id: str, payload: str) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        def _send() -> None:
            dead: set[asyncio.Queue[str]] = set()
            for queue in list(self._video_clients.get(video_id, set())):
                try:
                    queue.put_nowait(payload)
                except Exception:
                    dead.add(queue)
            if dead:
                self._video_clients[video_id] = self._video_clients.get(video_id, set()) - dead
                self._clients -= dead

        loop.call_soon_threadsafe(_send)

    def broadcast(self, video: dict[str, Any], event_type: str = "video_updated") -> None:
        self._broadcast(json.dumps({"type": event_type, "video": video}))

    def broadcast_delete(self, video_id: str) -> None:
        self._broadcast(json.dumps({"type": "video_deleted", "video_id": video_id}))

    def broadcast_video_detail(
        self,
        video_id: str,
        video: dict[str, Any],
        phase_runs: list[dict[str, Any]],
        transcription_runs: list[dict[str, Any]],
    ) -> None:
        self._broadcast(
            json.dumps(
                {
                    "type": "video_detail_updated",
                    "video_id": video_id,
                    "video": video,
                    "phase_runs": phase_runs,
                    "transcription_runs": transcription_runs,
                }
            )
        )
        self._broadcast_to_video(
            video_id,
            json.dumps(
                {
                    "type": "phase_runs_updated",
                    "video_id": video_id,
                    "video": video,
                    "phase_runs": phase_runs,
                    "transcription_runs": transcription_runs,
                }
            ),
        )

    def broadcast_package_ready(self, download_url: str) -> None:
        self._broadcast(json.dumps({"type": "package_ready", "download_url": download_url}))


class JobEventManager:
    """Manages Server-Sent Events (SSE) connections for workspace job updates."""

    MAX_CLIENTS = 100

    def __init__(self) -> None:
        self._clients: dict[str, set[asyncio.Queue[str]]] = {}
        self._stop_events: dict[asyncio.Queue[str], asyncio.Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_workspace_queues(self, workspace_id: str) -> set[asyncio.Queue[str]]:
        return self._clients.setdefault(workspace_id, set())

    def _cleanup_empty_workspace(self, workspace_id: str) -> None:
        queues = self._clients.get(workspace_id)
        if queues is not None and len(queues) == 0:
            self._clients.pop(workspace_id, None)

    async def connect(self, request: Request, workspace_id: str) -> StreamingResponse:
        total = sum(len(qs) for qs in self._clients.values())
        if total >= self.MAX_CLIENTS:
            oldest_workspace = next(iter(self._clients))
            oldest_queue = next(iter(self._clients[oldest_workspace]))
            self._clients[oldest_workspace].discard(oldest_queue)
            stop_event = self._stop_events.pop(oldest_queue, None)
            if stop_event is not None:
                stop_event.set()
            self._cleanup_empty_workspace(oldest_workspace)

        queues = self._get_workspace_queues(workspace_id)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        queues.add(queue)
        stop_event = asyncio.Event()
        self._stop_events[queue] = stop_event

        async def event_stream():
            try:
                while not stop_event.is_set():
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except TimeoutError:
                        if stop_event.is_set():
                            return
                        yield ":heartbeat\n\n"
                    else:
                        if stop_event.is_set():
                            return
                        yield f"data: {data}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                queues.discard(queue)
                self._cleanup_empty_workspace(workspace_id)
                self._stop_events.pop(queue, None)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def _broadcast(self, workspace_id: str, payload: str) -> None:
        loop = self._loop
        if loop is None:
            return

        def _send() -> None:
            queues = self._clients.get(workspace_id)
            if not queues:
                return
            dead: set[asyncio.Queue[str]] = set()
            for queue in list(queues):
                try:
                    queue.put_nowait(payload)
                except Exception:
                    dead.add(queue)
            if dead:
                for dq in dead:
                    stop_event = self._stop_events.pop(dq, None)
                    if stop_event is not None:
                        stop_event.set()
                queues -= dead
                self._cleanup_empty_workspace(workspace_id)

        if loop.is_running():
            loop.call_soon_threadsafe(_send)
        else:
            # Synchronous fallback is single-threaded/test-only; do not call from a
            # background thread while the loop is set but not running.
            _send()

    def _build_payload(
        self,
        event_type: str,
        workspace_id: str,
        stats: dict[str, int],
        job_id: str | None = None,
        jobs: list[dict[str, Any]] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "type": event_type,
            "workspace_id": workspace_id,
            "stats": stats,
        }
        if job_id is not None:
            payload["job_id"] = job_id
        if jobs is not None:
            payload["jobs"] = jobs
        return json.dumps(payload)

    def broadcast_jobs_created(
        self,
        workspace_id: str,
        jobs: list[dict[str, Any]],
        stats: dict[str, int],
    ) -> None:
        self._broadcast(
            workspace_id,
            self._build_payload("jobs_created", workspace_id, stats, jobs=jobs),
        )

    def broadcast_job_updated(
        self,
        workspace_id: str,
        job_id: str,
        stats: dict[str, int],
    ) -> None:
        self._broadcast(
            workspace_id,
            self._build_payload("job_updated", workspace_id, stats, job_id=job_id),
        )

    def broadcast_job_deleted(
        self,
        workspace_id: str,
        job_id: str,
        stats: dict[str, int],
    ) -> None:
        self._broadcast(
            workspace_id,
            self._build_payload("job_deleted", workspace_id, stats, job_id=job_id),
        )


def broadcast_job_update(
    job_db: JobQueries | None,
    job_event_manager: JobEventManager | None,
    job_id: str,
) -> None:
    try:
        if job_event_manager is None or job_db is None:
            return
        job = job_db.get_job(job_id)
        if job is None:
            return
        workspace_id = str(job.get("workspace_id", ""))
        if not workspace_id:
            return
        stats = job_db.count_jobs_by_status(workspace_id)
        job_event_manager.broadcast_job_updated(workspace_id, job_id, stats)
    except Exception:
        logger.exception("Failed to broadcast job update for %s", job_id)
