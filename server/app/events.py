import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse


class VideoEventManager:
    """Manages Server-Sent Events (SSE) connections for video status updates."""

    # Cap concurrent SSE connections to prevent memory/CPU leaks from reconnect storms
    MAX_CLIENTS = 100

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
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

        loop.call_soon_threadsafe(_send)

    def broadcast(self, video: dict[str, Any], event_type: str = "video_updated") -> None:
        self._broadcast(json.dumps({"type": event_type, "video": video}))

    def broadcast_delete(self, video_id: str) -> None:
        self._broadcast(json.dumps({"type": "video_deleted", "video_id": video_id}))
