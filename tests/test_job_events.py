import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from server.app.events import JobEventManager


@pytest.fixture
def manager():
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    return m


def test_broadcast_jobs_created_queues_message(manager):
    queue = asyncio.Queue()
    manager._get_workspace_queues("ws1").add(queue)
    manager.broadcast_jobs_created("ws1", [{"id": "j1"}], {"pending": 1})
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "jobs_created"' in data
    assert '"workspace_id": "ws1"' in data


def test_broadcast_isolated_by_workspace(manager):
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    manager._get_workspace_queues("ws1").add(q1)
    manager._get_workspace_queues("ws2").add(q2)
    manager.broadcast_job_updated("ws1", "j1", {"pending": 1})
    assert not q1.empty()
    assert q2.empty()


def test_connect_evicts_oldest_at_capacity():
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    m.MAX_CLIENTS = 2
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    m._get_workspace_queues("ws1").add(q1)
    m._get_workspace_queues("ws2").add(q2)

    async def add_third() -> None:
        request = MagicMock(spec=Request)
        await m.connect(request, "ws3")

    asyncio.set_event_loop(m._loop)
    m._loop.run_until_complete(add_third())

    assert q1 not in m._get_workspace_queues("ws1")
    assert q2 in m._get_workspace_queues("ws2")
