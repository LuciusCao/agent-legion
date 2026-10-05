import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from server.app.events.bus import _EVICTED, EventBus, workspace_channel

HEARTBEAT_SECONDS = 15.0


def heartbeat_frame() -> str:
    """#914/#885: the keep-alive is a named ``heartbeat`` event carrying data,
    not an SSE comment — EventSource never hands comment lines to JS, so the
    browser could not tell a live-but-quiet stream from a silently hung one.
    The payload advertises the interval, so the frontend watchdog derives its
    stall timeout from this single constant instead of a parallel literal.

    Backward compatible: EventSource only dispatches named events to listeners
    registered for that type (``onmessage`` sees unnamed ``message`` events
    only), and line-based clients see a ``data:`` object without ``type``."""
    interval_ms = int(HEARTBEAT_SECONDS * 1000)
    return f"event: heartbeat\ndata: {json.dumps({'interval_ms': interval_ms})}\n\n"


class JobEventManager:
    """Manages Server-Sent Events (SSE) connections for workspace job updates."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def connect(
        self,
        request: Request,
        channel: str,
        payload_filter: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> StreamingResponse:
        """``payload_filter`` (#881) rewrites or drops (None) each payload for
        this connection — per-subscriber visibility on a broadcast channel."""
        bus = self.bus
        queue = bus.subscribe(channel)

        async def event_stream():
            # Flush headers immediately so proxies (e.g. Vite dev server) forward
            # the SSE connection and browsers fire onopen without waiting for the
            # first real event or heartbeat timeout.
            yield ":ok\n\n"
            # #914: one heartbeat up front arms the client watchdog at once,
            # so a stream that hangs before the first periodic beat is caught.
            yield heartbeat_frame()
            loop = asyncio.get_running_loop()
            # Heartbeat clock runs from the last frame actually sent, so a
            # stream of filtered-out events cannot starve the keep-alive. The
            # heartbeat is yielded here, never routed through the bus queue,
            # so ``payload_filter`` (#881, drops unknown payloads fail-closed)
            # cannot swallow it on restricted connections.
            last_sent = loop.time()
            try:
                while True:
                    remaining = max(HEARTBEAT_SECONDS - (loop.time() - last_sent), 0.0)
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=remaining)
                    except TimeoutError:
                        yield heartbeat_frame()
                        last_sent = loop.time()
                        continue
                    if data is _EVICTED:
                        return
                    if payload_filter is not None:
                        data = await payload_filter(data)
                        if data is None:
                            continue
                    yield f"data: {data}\n\n"
                    last_sent = loop.time()
            except asyncio.CancelledError:
                raise
            finally:
                bus.unsubscribe(channel, queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

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
        self.bus.publish(
            workspace_channel(workspace_id),
            self._build_payload("jobs_created", workspace_id, stats, jobs=jobs),
        )

    def broadcast_job_updated(
        self,
        workspace_id: str,
        job_id: str,
        stats: dict[str, int],
    ) -> None:
        self.bus.publish(
            workspace_channel(workspace_id),
            self._build_payload("job_updated", workspace_id, stats, job_id=job_id),
        )

    def broadcast_job_deleted(
        self,
        workspace_id: str,
        job_id: str,
        stats: dict[str, int],
    ) -> None:
        self.bus.publish(
            workspace_channel(workspace_id),
            self._build_payload("job_deleted", workspace_id, stats, job_id=job_id),
        )
