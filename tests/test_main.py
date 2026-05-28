import asyncio
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from server.app.main import create_app


def test_create_app_with_worker_start_lifespan(tmp_path):
    with (
        patch("server.app.main.WorkerThread") as MockWT,
        patch("server.app.main.recover_interrupted_videos") as mock_recover,
        patch("server.app.main.RunnerPool") as MockPool,
        patch("server.app.main.AgentStatusManager") as MockAgent,
    ):
        mock_agent = MagicMock()
        mock_agent.agents = []
        MockAgent.return_value = mock_agent

        mock_pool = MagicMock()
        mock_pool.size.return_value = 0
        mock_pool.all_runners.return_value = []
        MockPool.from_settings.return_value = mock_pool

        mock_thread = MagicMock()
        MockWT.return_value = mock_thread

        app = create_app(data_dir=tmp_path, start_worker=True)

        async def _test():
            async with app.router.lifespan_context(app):
                pass

        asyncio.run(_test())

        mock_recover.assert_called_once()
        MockPool.from_settings.assert_called_once()
        mock_thread.start.assert_called_once()
        mock_thread.stop.assert_called_once()


def test_frontend_missing_route(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Video Hive API" in response.text
