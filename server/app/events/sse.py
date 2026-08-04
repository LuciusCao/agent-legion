import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from server.app.events.bus import _EVICTED, EventBus, workspace_channel


class JobEventManager:
    """Manages Server-Sent Events (SSE) connections for workspace job updates."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def connect(self, request: Request, channel: str) -> StreamingResponse:
        bus = self.bus
        queue = bus.subscribe(channel)

        async def event_stream():
            # Flush headers immediately so proxies (e.g. Vite dev server) forward
            # the SSE connection and browsers fire onopen without waiting for the
            # first real event or heartbeat timeout.
            yield ":ok\n\n"
            try:
                while True:
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except TimeoutError:
                        yield ":heartbeat\n\n"
                        continue
                    if data is _EVICTED:
                        return
                    yield f"data: {data}\n\n"
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
