import asyncio
import json
import subprocess


def test_agents_websocket_sends_initial_list(client, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps([{"id": "main", "identityName": "Main"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agent_manager = client.app.state.agent_manager
    agent_manager.discover()

    with client.websocket_connect("/api/agents") as ws:
        data = ws.receive_json()
        assert len(data) == 1
        assert data[0]["id"] == "main"
        assert data[0]["name"] == "Main"
        assert data[0]["busy"] is False


def test_agents_websocket_broadcasts_busy_idle_updates(client, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps([{"id": "main"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agent_manager = client.app.state.agent_manager
    agent_manager.discover()

    with client.websocket_connect("/api/agents") as ws:
        ws.receive_json()
        agent_manager.set_busy(
            "main",
            {
                "id": "v1",
                "title": "T1",
                "content_type": "knowledge",
                "external_id": "K001",
                "current_phase": "download",
            },
        )
        data = ws.receive_json()
        assert data[0]["busy"] is True
        assert data[0]["current_video_id"] == "v1"
        assert data[0]["current_title"] == "T1"

        agent_manager.set_idle("main")
        data = ws.receive_json()
        assert data[0]["busy"] is False
        assert data[0]["current_video_id"] is None


def test_video_event_manager_broadcast():
    import json

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        manager._clients.add(queue)

        manager.broadcast({"id": "v1", "status": "running"})
        await asyncio.sleep(0)  # yield so call_soon callback runs
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "video_updated"
        assert data["video"]["id"] == "v1"

        manager.broadcast_delete("v1")
        await asyncio.sleep(0)
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "video_deleted"
        assert data["video_id"] == "v1"

    asyncio.run(_test())


def test_video_event_manager_max_clients():
    from unittest.mock import MagicMock

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()

        for _ in range(VideoEventManager.MAX_CLIENTS + 5):
            await manager.connect(MagicMock())

        assert len(manager._clients) == VideoEventManager.MAX_CLIENTS

    asyncio.run(_test())


def test_video_event_manager_queue_full_cleanup():

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()
        full_queue = asyncio.Queue(maxsize=1)
        full_queue.put_nowait("block")
        manager._clients.add(full_queue)

        normal_queue = asyncio.Queue()
        manager._clients.add(normal_queue)

        manager.broadcast({"id": "v1", "status": "running"})
        await asyncio.sleep(0)

        # Full queue should have been evicted
        assert full_queue not in manager._clients
        # Normal queue should have received the message
        payload = await asyncio.wait_for(normal_queue.get(), timeout=1.0)
        assert "v1" in payload

    asyncio.run(_test())


def test_broadcast_package_ready():

    from server.app.events import VideoEventManager

    async def _test():
        mgr = VideoEventManager()
        mgr._loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        mgr._clients.add(queue)

        mgr.broadcast_package_ready("/api/packages/test.zip")
        await asyncio.sleep(0)
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "package_ready"
        assert data["download_url"] == "/api/packages/test.zip"

    asyncio.run(_test())
