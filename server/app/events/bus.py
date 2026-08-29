from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

_EVICTED: object = object()
"""投递到被驱逐订阅者队列的哨兵；订阅方收到后应立即结束流。"""


def workspace_channel(workspace_id: str) -> str:
    return f"workspace:{workspace_id}"


class EventBus(Protocol):
    """进程内事件总线：channel 命名空间 + 有界订阅队列；publish 线程安全。"""

    def attach_loop(self, loop: asyncio.AbstractEventLoop | None) -> None: ...

    def publish(self, channel: str, payload: str) -> None: ...

    def subscribe(self, channel: str) -> asyncio.Queue: ...

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None: ...


class InProcessEventBus:
    """默认进程内实现，承接原 JobEventManager 的驱逐与有界队列语义。"""

    MAX_CLIENTS = 100
    QUEUE_MAXSIZE = 64

    def __init__(self) -> None:
        # dict 保持插入序，保证驱逐的是全局最旧订阅者。
        self._subscribers: dict[str, dict[asyncio.Queue, None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def subscribe(self, channel: str) -> asyncio.Queue:
        total = sum(len(qs) for qs in self._subscribers.values())
        if total >= self.MAX_CLIENTS:
            self._evict_oldest()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._subscribers.setdefault(channel, {})[queue] = None
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(channel)
        if queues is None:
            return
        queues.pop(queue, None)
        if not queues:
            self._subscribers.pop(channel, None)

    def publish(self, channel: str, payload: str) -> None:
        loop = self._loop
        if loop is None:
            self._send(channel, payload)
            return
        try:
            # Race window: the loop can stop between the is_running check and
            # call_soon_threadsafe (the latter then raises RuntimeError). A
            # publish racing shutdown must degrade to a direct send (same as
            # no loop attached), never propagate into the publishing thread.
            if loop.is_running():
                loop.call_soon_threadsafe(self._send, channel, payload)
            else:
                self._send(channel, payload)
        except RuntimeError:
            self._send(channel, payload)

    def _send(self, channel: str, payload: str) -> None:
        queues = self._subscribers.get(channel)
        if not queues:
            return
        dead: set[asyncio.Queue] = set()
        for queue in list(queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow subscriber falling QUEUE_MAXSIZE events behind: evict it
                # with the sentinel (dropping the oldest queued item to make
                # room) so its stream ends and the client reconnects/resyncs,
                # instead of leaving it on a heartbeat-only zombie connection.
                # #204 suppress audit: the two suppressed calls below can only
                # fail in the QueueFull race (the queue filled between the
                # except above and the room-making get_nowait) — the sentinel
                # then never lands, but the eviction below still removes the
                # subscriber, which is the whole point; the dropped payload is
                # already lost by definition of the overflow. Nothing else is
                # suppressible here (get/put on an unbounded asyncio.Queue
                # have no other failure mode), so the suppression cannot eat a
                # programming error from unrelated code.
                with contextlib.suppress(Exception):
                    queue.get_nowait()
                    queue.put_nowait(_EVICTED)
                dead.add(queue)
            except Exception:
                # #204 audit (PR #251): a non-QueueFull failure on put marks
                # the subscriber as dead — an unbounded asyncio.Queue has no
                # other failure mode, so anything landing here means the
                # connection is gone; removing it protects the fan-out for
                # the remaining subscribers. The QueueFull race itself is
                # handled by the suppress branch above.
                dead.add(queue)
        for queue in dead:
            self.unsubscribe(channel, queue)

    def _evict_oldest(self) -> None:
        for channel in list(self._subscribers):
            queues = self._subscribers.get(channel)
            if not queues:
                continue
            oldest = next(iter(queues))
            # #204 suppress audit: same single-purpose suppression as in
            # _send — only the QueueFull race on the room-making put_nowait
            # can be suppressed, and the eviction itself does not depend on
            # the sentinel landing (the client's stream end is confirmed by
            # unsubscribe + the subscribe-side MAX_CLIENTS check). A failure
            # to enqueue the sentinel merely means the evicted client sees
            # its stream end on the next reconnect instead.
            with contextlib.suppress(Exception):
                oldest.put_nowait(_EVICTED)
            self.unsubscribe(channel, oldest)
            return
