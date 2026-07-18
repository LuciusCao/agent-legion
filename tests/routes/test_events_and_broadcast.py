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


def test_agents_websocket_clean_disconnect_stays_silent(caplog):
    import logging

    from fastapi import WebSocketDisconnect
    from fastapi.routing import APIWebSocketRoute

    from server.app.agents import AgentStatusManager
    from server.app.routes.agents import create_agents_router

    class _FakeWebSocket:
        async def accept(self):
            pass

        async def send_json(self, data):
            pass

        async def receive_text(self):
            raise WebSocketDisconnect()

    async def _test():
        manager = AgentStatusManager()
        router = create_agents_router(manager)
        ws_route = next(r for r in router.routes if isinstance(r, APIWebSocketRoute))
        ws = _FakeWebSocket()
        with caplog.at_level(logging.WARNING, logger="server.app.routes.agents"):
            await ws_route.endpoint(ws)
        assert ws not in manager._clients

    asyncio.run(_test())
    assert caplog.records == []


def test_agents_websocket_logs_unexpected_receive_error(caplog):
    import logging

    from fastapi.routing import APIWebSocketRoute

    from server.app.agents import AgentStatusManager
    from server.app.routes.agents import create_agents_router

    class _FakeWebSocket:
        async def accept(self):
            pass

        async def send_json(self, data):
            pass

        async def receive_text(self):
            raise RuntimeError("boom")

    async def _test():
        manager = AgentStatusManager()
        router = create_agents_router(manager)
        ws_route = next(r for r in router.routes if isinstance(r, APIWebSocketRoute))
        ws = _FakeWebSocket()
        await ws_route.endpoint(ws)
        assert ws not in manager._clients

    with caplog.at_level(logging.ERROR, logger="server.app.routes.agents"):
        asyncio.run(_test())
    assert any("receive loop failed" in record.getMessage() for record in caplog.records)
