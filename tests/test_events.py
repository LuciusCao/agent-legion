import asyncio
import contextlib
import json
from unittest.mock import MagicMock, patch

import pytest

from server.app.events import VideoEventManager


@pytest.fixture
def event_manager():
    manager = VideoEventManager()
    manager._loop = None
    yield manager
    # Reset state after test
    manager._clients.clear()
    manager._video_clients.clear()
    manager._loop = None


async def _set_loop(manager: VideoEventManager) -> None:
    manager._loop = asyncio.get_running_loop()


def test_connect_video_yields_heartbeat_and_cleans_up_on_disconnect(event_manager):
    async def _test():
        await _set_loop(event_manager)
        request = MagicMock()

        # Patch wait_for to immediately timeout so we receive a heartbeat.
        async def fake_wait_for(coro, timeout):
            # Cancel the underlying coroutine to avoid "never awaited" warnings.
            task = asyncio.ensure_future(coro)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise TimeoutError()

        with patch("server.app.events.asyncio.wait_for", fake_wait_for):
            response = await event_manager.connect_video(request, "v1")
            gen = response.body_iterator
            chunk = await anext(gen)
            assert chunk == ":heartbeat\n\n"
            await gen.aclose()

        # Cleanup: queue removed from both indexes; empty video bucket may remain.
        assert len(event_manager._clients) == 0
        assert all(len(queues) == 0 for queues in event_manager._video_clients.values())

    asyncio.run(_test())


def test_connect_video_yields_payload_and_cleans_up(event_manager):
    async def _test():
        await _set_loop(event_manager)
        request = MagicMock()
        response = await event_manager.connect_video(request, "v1")
        gen = response.body_iterator

        # The queue was added to both indexes.
        assert len(event_manager._clients) == 1
        assert len(event_manager._video_clients.get("v1", set())) == 1

        queue = next(iter(event_manager._video_clients["v1"]))
        queue.put_nowait(json.dumps({"type": "test", "data": "hello"}))

        chunk = await asyncio.wait_for(anext(gen), timeout=1.0)
        assert "data: {" in chunk
        assert "hello" in chunk

        await gen.aclose()

        assert len(event_manager._clients) == 0
        assert len(event_manager._video_clients.get("v1", set())) == 0

    asyncio.run(_test())


def test_broadcast_to_video_only_targets_subscribed_clients(event_manager):
    async def _test():
        await _set_loop(event_manager)

        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        q_other = asyncio.Queue()

        event_manager._clients.update({q1, q2, q_other})
        event_manager._video_clients["v1"] = {q1, q2}
        event_manager._video_clients["v2"] = {q_other}

        event_manager._broadcast_to_video("v1", json.dumps({"msg": "for-v1"}))
        await asyncio.sleep(0)  # let call_soon callback run

        assert await asyncio.wait_for(q1.get(), timeout=1.0) == json.dumps({"msg": "for-v1"})
        assert await asyncio.wait_for(q2.get(), timeout=1.0) == json.dumps({"msg": "for-v1"})
        assert q_other.empty()

    asyncio.run(_test())


def test_broadcast_to_video_leaves_unrelated_video_clients_intact(event_manager):
    async def _test():
        await _set_loop(event_manager)

        q_v1 = asyncio.Queue()
        q_v2 = asyncio.Queue()

        event_manager._clients.update({q_v1, q_v2})
        event_manager._video_clients["v1"] = {q_v1}
        event_manager._video_clients["v2"] = {q_v2}

        event_manager._broadcast_to_video("v2", json.dumps({"msg": "for-v2"}))
        await asyncio.sleep(0)

        assert q_v1.empty()
        assert await asyncio.wait_for(q_v2.get(), timeout=1.0) == json.dumps({"msg": "for-v2"})

        # Indexes untouched for v1.
        assert q_v1 in event_manager._clients
        assert q_v1 in event_manager._video_clients["v1"]

    asyncio.run(_test())


def test_dead_video_clients_removed_from_both_indexes(event_manager):
    async def _test():
        await _set_loop(event_manager)

        # A full queue will raise QueueFull on put_nowait, simulating a dead client.
        dead_queue = asyncio.Queue(maxsize=1)
        dead_queue.put_nowait("block")

        live_queue = asyncio.Queue()

        event_manager._clients.update({dead_queue, live_queue})
        event_manager._video_clients["v1"] = {dead_queue, live_queue}

        event_manager._broadcast_to_video("v1", json.dumps({"msg": "test"}))
        await asyncio.sleep(0)

        # Dead client evicted from both indexes.
        assert dead_queue not in event_manager._clients
        assert dead_queue not in event_manager._video_clients["v1"]

        # Live client still there and received the message.
        assert live_queue in event_manager._clients
        assert live_queue in event_manager._video_clients["v1"]
        assert await asyncio.wait_for(live_queue.get(), timeout=1.0) == json.dumps({"msg": "test"})

    asyncio.run(_test())


def test_broadcast_removes_dead_clients_from_global_and_video_indexes(event_manager):
    async def _test():
        await _set_loop(event_manager)

        dead_queue = asyncio.Queue(maxsize=1)
        dead_queue.put_nowait("block")

        live_queue_v1 = asyncio.Queue()
        live_queue_v2 = asyncio.Queue()

        event_manager._clients.update({dead_queue, live_queue_v1, live_queue_v2})
        event_manager._video_clients["v1"] = {dead_queue, live_queue_v1}
        event_manager._video_clients["v2"] = {live_queue_v2}

        event_manager.broadcast({"id": "v1", "status": "running"})
        await asyncio.sleep(0)

        assert dead_queue not in event_manager._clients
        assert dead_queue not in event_manager._video_clients.get("v1", set())

        # Live queues remain; dead queue is removed from v1.
        assert live_queue_v1 in event_manager._clients
        assert live_queue_v2 in event_manager._clients
        assert dead_queue not in event_manager._video_clients["v1"]
        assert live_queue_v1 in event_manager._video_clients["v1"]
        assert live_queue_v2 in event_manager._video_clients["v2"]

        # Both live queues received the broadcast.
        assert await asyncio.wait_for(live_queue_v1.get(), timeout=1.0)
        assert await asyncio.wait_for(live_queue_v2.get(), timeout=1.0)

    asyncio.run(_test())
