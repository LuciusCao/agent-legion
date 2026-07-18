import asyncio
import json
import logging
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

# Backwards-compatible re-exports that now live in server.app.job_events.
from server.app.job_events import broadcast_job_update as broadcast_job_update
from server.app.job_events import record_job_update as record_job_update

logger = logging.getLogger(__name__)


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
            # Flush headers immediately so proxies (e.g. Vite dev server) forward
            # the SSE connection and browsers fire onopen without waiting for the
            # first real event or heartbeat timeout.
            yield ":ok\n\n"
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
