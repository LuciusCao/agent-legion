import asyncio
import threading

from server.app.events.bus import _EVICTED, InProcessEventBus


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


def test_bounded_queue_drops_oldest_for_replaceable_payload():
    """#563：可丢帧（流式 text 快照，publish 声明 replaceable=True）的瞬时
    慢消费不再驱逐——丢最旧腾位后投递最新事件，订阅保留。"""
    bus = InProcessEventBus()
    queue = bus.subscribe("studio-chat:s1")
    for _ in range(bus.QUEUE_MAXSIZE):
        bus.publish("studio-chat:s1", "x", replaceable=True)
    bus.publish("studio-chat:s1", "overflow", replaceable=True)  # 队满 → 丢最旧、投递最新，不驱逐
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    # 最旧一条被丢弃腾位，最新事件在队尾，订阅者仍在册。
    assert items[-1] == "overflow"
    assert len(items) == bus.QUEUE_MAXSIZE
    assert queue in bus._subscribers.get("studio-chat:s1", {})


def test_bounded_queue_evicts_on_sustained_replaceable_overflow():
    """#563：可丢帧连续溢出达到阈值（真死连接）才驱逐——哨兵结束流。"""
    bus = InProcessEventBus()
    queue = bus.subscribe("studio-chat:s1")
    for _ in range(bus.QUEUE_MAXSIZE + 5):
        bus.publish("studio-chat:s1", "x", replaceable=True)
    # 队已满且持续不消费：每次 publish 都是"丢最旧 + 投递最新 + 计数 +1"。
    for index in range(bus.QUEUE_MAXSIZE + 10):
        bus.publish("studio-chat:s1", f"burst-{index}", replaceable=True)
        if queue not in bus._subscribers.get("studio-chat:s1", {}):
            break
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    # 驱逐哨兵在队尾（腾位后投递），流结束、客户端重连后 resync。
    assert items[-1] is _EVICTED
    assert "studio-chat:s1" not in bus._subscribers or queue not in bus._subscribers.get(
        "studio-chat:s1", set()
    )


def test_non_replaceable_payload_evicts_immediately_on_overflow():
    """#611 codex P2：不可丢事件（tool_call/permission/status 持久消息、
    workspace job 补丁的 revision 水位消费）不做丢最旧——静默丢帧让消费方
    缺消息且无重连触发；立即驱逐断流 → SSE 重连 + 全量回取是既有的无损
    自愈路径。同一 channel 上只有 replaceable=True 的 publish 走丢旧。"""
    bus = InProcessEventBus()
    queue = bus.subscribe("studio-chat:s1")
    for _ in range(bus.QUEUE_MAXSIZE):
        bus.publish("studio-chat:s1", "x")  # 未声明 replaceable
    bus.publish("studio-chat:s1", "overflow")  # 队满 → 立即驱逐 + 哨兵
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    assert items[-1] is _EVICTED
    assert "studio-chat:s1" not in bus._subscribers


def test_overflow_counter_resets_on_successful_delivery():
    """#563：一次成功投递清零连续溢出计数——间歇慢消费不累积到驱逐。"""
    bus = InProcessEventBus()
    queue = bus.subscribe("studio-chat:s1")
    for _ in range(bus.QUEUE_MAXSIZE + 3):
        bus.publish("studio-chat:s1", "x", replaceable=True)
    assert queue in bus._subscribers.get("studio-chat:s1", {})  # 未达阈值
    queue.get_nowait()  # 腾出一个位置，下一次 publish 成功投递
    bus.publish("studio-chat:s1", "fresh", replaceable=True)  # 计数清零
    assert bus._overflows[queue] == 0
    assert queue in bus._subscribers.get("studio-chat:s1", {})


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
