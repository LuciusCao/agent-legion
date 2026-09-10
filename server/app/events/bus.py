from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

_EVICTED: object = object()
"""投递到被驱逐订阅者队列的哨兵；订阅方收到后应立即结束流。"""

# #563：QueueFull 时先丢最旧腾位再投递，只有连续溢出达到该阈值才驱逐。
# 流式事件（studio chat 的 text 快照帧）是全量语义——丢中间帧无损，最新
# 帧到达即自愈；立刻驱逐会把短暂慢消费（前端合盖/后台节流）升级成断流
# 重连，而重连空窗盖过 turn 结尾正是 #563 截断的触发形态。连续溢出说明
# 消费端真的死了（驱逐防心跳僵尸连接的原语义保留）。
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
        self._overflows.pop(queue, None)
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
                self._overflows[queue] = 0
            except asyncio.QueueFull:
                # #563：慢消费先丢最旧腾位再投递一次（全量快照语义下丢中间
                # 帧无损）；只有连续 OVERFLOW_EVICT_THRESHOLD 次腾位仍满才
                # 驱逐（真死连接，心跳僵尸防护原语义保留）。
                # #204 broad-except audit: the suppressed calls below can only
                # fail in the QueueFull race (the queue filled between the
                # except above and the room-making get_nowait) — the retry put
                # then simply drops this payload (already lost by definition of
                # the overflow); nothing else is suppressible on an unbounded
                # asyncio.Queue, so the suppression cannot eat a programming
                # error from unrelated code.
                overflows = self._overflows.get(queue, 0) + 1
                self._overflows[queue] = overflows
                with contextlib.suppress(Exception):
                    queue.get_nowait()
                    queue.put_nowait(payload)
                if overflows < OVERFLOW_EVICT_THRESHOLD:
                    continue
                # Evict with the sentinel (dropping the oldest queued item to
                # make room) so its stream ends and the client
                # reconnects/resyncs, instead of leaving it on a
                # heartbeat-only zombie connection.
                with contextlib.suppress(Exception):
                    queue.get_nowait()
                    queue.put_nowait(_EVICTED)
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

    def _evict_oldest(self) -> None:
        for channel in list(self._subscribers):
            queues = self._subscribers.get(channel)
            if not queues:
                continue
            oldest = next(iter(queues))
            # #204 broad-except audit: same single-purpose suppression as in
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
