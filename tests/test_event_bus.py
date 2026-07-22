import asyncio
import threading

from server.app.event_bus import _EVICTED, InProcessEventBus


def test_subscribe_receive_and_unsubscribe_idempotent():
    bus = InProcessEventBus()
    bus.attach_loop(None)  # type: ignore[arg-type]  # loop None → 同步直发路径

    queue = bus.subscribe("workspace:ws1")
    bus.publish("workspace:ws1", '{"type":"job_updated"}')
    assert queue.get_nowait() == '{"type":"job_updated"}'

    bus.unsubscribe("workspace:ws1", queue)
    bus.unsubscribe("workspace:ws1", queue)  # 幂等，不抛异常
    bus.publish("workspace:ws1", "ignored")
    assert queue.empty()


def test_publish_isolated_by_channel():
    bus = InProcessEventBus()
    q1 = bus.subscribe("workspace:ws1")
    q2 = bus.subscribe("workspace:ws2")
    bus.publish("workspace:ws1", "a")
    assert q1.get_nowait() == "a"
    assert q2.empty()


def test_bounded_queue_evicts_slow_subscriber_with_sentinel():
    bus = InProcessEventBus()
    queue = bus.subscribe("dashboard")
    for _ in range(bus.QUEUE_MAXSIZE):
        bus.publish("dashboard", "x")
    bus.publish("dashboard", "overflow")  # 队满 → 驱逐并投递哨兵
    bus.publish("dashboard", "after-removal")
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    # 最旧事件被丢弃以腾出位置，哨兵让流结束、客户端重连后 resync。
    assert items[-1] is _EVICTED
    assert len(items) == bus.QUEUE_MAXSIZE
    assert "dashboard" not in bus._subscribers or queue not in bus._subscribers.get(
        "dashboard", set()
    )


def test_eviction_at_max_clients_sends_sentinel():
    bus = InProcessEventBus()
    queues = [bus.subscribe(f"workspace:ws{i % 3}") for i in range(bus.MAX_CLIENTS)]
    oldest = queues[0]
    bus.subscribe("workspace:new")  # 超过 MAX_CLIENTS → 驱逐最旧
    assert oldest.get_nowait() is _EVICTED
    assert all(oldest not in subs for subs in bus._subscribers.values())


def test_publish_from_worker_thread_via_call_soon_threadsafe():
    async def _run():
        bus = InProcessEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        queue = bus.subscribe("agents")
        done = threading.Event()

        def _publisher():
            bus.publish("agents", "payload")
            done.set()

        thread = threading.Thread(target=_publisher)
        thread.start()
        thread.join(timeout=5)
        assert done.is_set()
        assert await asyncio.wait_for(queue.get(), timeout=2) == "payload"

    asyncio.run(_run())


def test_attach_loop_replaces_loop():
    bus = InProcessEventBus()
    bus.attach_loop(None)  # type: ignore[arg-type]
    assert bus._loop is None
