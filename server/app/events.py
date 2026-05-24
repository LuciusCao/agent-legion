import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse


class VideoEventManager:
    """Manages Server-Sent Events (SSE) connections for video status updates."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, request: Request) -> StreamingResponse:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._clients.add(queue)
        self._loop = asyncio.get_running_loop()

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"data: {data}\n\n"
                    except TimeoutError:
                        yield ":heartbeat\n\n"
            finally:
                self._clients.discard(queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def broadcast(self, video: dict[str, Any], event_type: str = "video_updated") -> None:
        payload = json.dumps({"type": event_type, "video": video})
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for queue in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception:
                self._clients.discard(queue)

    def broadcast_delete(self, video_id: str) -> None:
        payload = json.dumps({"type": "video_deleted", "video_id": video_id})
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for queue in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception:
                self._clients.discard(queue)
