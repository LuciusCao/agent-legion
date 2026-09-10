from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

_EVICTED: object = object()
"""投递到被驱逐订阅者队列的哨兵；订阅方收到后应立即结束流。"""

# #563：QueueFull 先丢最旧腾位再投递（流式帧是全量快照，丢中间帧无损），
# 只有连续溢出达到该阈值（真死连接，防心跳僵尸原语义）才驱逐——立即驱逐
# 引发的断流重连正是截断的触发形态。
OVERFLOW_EVICT_THRESHOLD = 64


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
        # #563：per-queue 连续溢出计数（成功投递清零）。
        self._overflows: dict[asyncio.Queue, int] = {}

    def attach_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def subscribe(self, channel: str) -> asyncio.Queue:
        if sum(len(qs) for qs in self._subscribers.values()) >= self.MAX_CLIENTS:
            self._evict_oldest()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._subscribers.setdefault(channel, {})[queue] = None
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(channel)
        if queues is None:
            return
        queues.pop(queue, None)
        self._overflows.pop(queue, None)
        if not queues:
            self._subscribers.pop(channel, None)

    def publish(self, channel: str, payload: str) -> None:
        # Race window: the loop can stop between the is_running check and
        # call_soon_threadsafe (the latter then raises RuntimeError). A
        # publish racing shutdown must degrade to a direct send (same as
        # no loop attached), never propagate into the publishing thread.
        loop = self._loop
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._send, channel, payload)
                return
        except RuntimeError:
            pass
        self._send(channel, payload)

    def _send(self, channel: str, payload: str) -> None:
        queues = self._subscribers.get(channel)
        if not queues:
            return
        dead: set[asyncio.Queue] = set()
        for queue in list(queues):
            try:
                queue.put_nowait(payload)
                self._overflows[queue] = 0
            except asyncio.QueueFull:
                if self._overflow_send(queue, payload):
                    dead.add(queue)
            except Exception:
                # #204 broad-except audit (PR #251): a non-QueueFull failure on put marks
                # the subscriber as dead — an unbounded asyncio.Queue has no
                # other failure mode, so anything landing here means the
                # connection is gone; removing it protects the fan-out for
                # the remaining subscribers. The QueueFull race itself is
                # handled by the suppress branch above.
                dead.add(queue)
        for queue in dead:
            self.unsubscribe(channel, queue)

    def _overflow_send(self, queue: asyncio.Queue, payload: str) -> bool:
        # #563 慢消费处理（返回是否驱逐）：丢最旧腾位投递最新（流式帧是
        # 全量快照，丢中间帧无损）；连续溢出达 OVERFLOW_EVICT_THRESHOLD
        # （真死连接，防心跳僵尸原语义）才投递哨兵——立即驱逐引发的断流
        # 重连正是 #563 截断的触发形态。
        #
        # #204 broad-except audit: the suppressed calls can only fail in the
        # QueueFull race — the retry put then drops this payload (already
        # lost by definition of the overflow); nothing else is suppressible
        # on an unbounded asyncio.Queue.
        overflows = self._overflows[queue] = self._overflows.get(queue, 0) + 1
        evict = overflows >= OVERFLOW_EVICT_THRESHOLD
        with contextlib.suppress(Exception):
            queue.get_nowait()
            queue.put_nowait(_EVICTED if evict else payload)
        return evict

    def _evict_oldest(self) -> None:
        for channel in list(self._subscribers):
            queues = self._subscribers.get(channel)
            if not queues:
                continue
            oldest = next(iter(queues))
            # #204 broad-except audit: same single-purpose suppression as in
            # _overflow_send — a failure to enqueue the sentinel merely means
            # the evicted client sees its stream end on the next reconnect.
            with contextlib.suppress(Exception):
                oldest.put_nowait(_EVICTED)
            self.unsubscribe(channel, oldest)
            return
