import time
from unittest.mock import MagicMock, patch

from server.app.worker_thread import WorkerThread


def test_worker_thread_starts_and_stops():
    mock_db = MagicMock()
    mock_db.list_videos.return_value = []
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_pool = MagicMock()
    mock_pool.size.return_value = 0
    mock_pool.acquire.side_effect = RuntimeError("No free runner")
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = False

    wt = WorkerThread(
        mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
    )
    wt.start()
    time.sleep(0.15)
    wt.stop(timeout=1)

    assert wt._thread is not None
    assert not wt._thread.is_alive()


def test_worker_loop_respects_pause():
    mock_db = MagicMock()
    mock_db.list_videos.return_value = []
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_pool = MagicMock()
    mock_pool.size.return_value = 0
    mock_pool.acquire.side_effect = RuntimeError("No free runner")
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = True

    with patch("server.app.worker.process_video_once") as mock_process:
        wt = WorkerThread(
            mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
        )
        wt.start()
        time.sleep(0.2)
        wt.stop(timeout=1)

        mock_process.assert_not_called()


def test_worker_loop_dispatches_local_task():
    mock_db = MagicMock()
    video = {
        "id": "v1",
        "status": "queued",
        "current_phase": "download",
        "content_type": "knowledge",
        "source_url": "https://example.com/v1.mp4",
        "title": "V1",
    }
    mock_db.list_videos.return_value = [video]
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_pool = MagicMock()
    mock_pool.size.return_value = 0
    mock_pool.acquire.side_effect = RuntimeError("No free runner")
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = False

    with patch("server.app.worker.process_video_once") as mock_process:
        mock_process.return_value = True
        wt = WorkerThread(
            mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
        )
        wt.start()
        time.sleep(0.3)
        wt.stop(timeout=1)

        mock_process.assert_called_once()
        args = mock_process.call_args
        assert args[0][2] == "v1"  # video_id


def test_worker_loop_dispatches_agent_task():
    mock_db = MagicMock()
    video = {
        "id": "v1",
        "status": "queued",
        "current_phase": "subtitle_review",
        "content_type": "knowledge",
        "source_url": "https://example.com/v1.mp4",
        "title": "V1",
    }
    mock_db.list_videos.return_value = [video]
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_runner = MagicMock()
    mock_runner.agent_id = "main"
    mock_pool = MagicMock()
    mock_pool.size.return_value = 1
    mock_pool.acquire.return_value = (0, mock_runner)
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = False

    with patch("server.app.worker.process_video_once") as mock_process:
        mock_process.return_value = True
        wt = WorkerThread(
            mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
        )
        wt.start()
        time.sleep(0.3)
        wt.stop(timeout=1)

        mock_process.assert_called_once()
        args = mock_process.call_args[0]
        assert args[4] is mock_runner


def test_worker_loop_sets_agent_busy_and_idle():
    mock_db = MagicMock()
    video = {
        "id": "v1",
        "status": "queued",
        "current_phase": "subtitle_review",
        "content_type": "knowledge",
        "source_url": "https://example.com/v1.mp4",
        "title": "V1",
    }
    mock_db.list_videos.return_value = [video]
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_runner = MagicMock()
    mock_runner.agent_id = "main"
    mock_pool = MagicMock()
    mock_pool.size.return_value = 1
    mock_pool.acquire.return_value = (0, mock_runner)
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = False

    with patch("server.app.worker.process_video_once") as mock_process:
        mock_process.return_value = True
        wt = WorkerThread(
            mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
        )
        wt.start()
        time.sleep(0.3)
        wt.stop(timeout=1)

        mock_agent_manager.set_busy.assert_called_once_with("main", video)
        mock_agent_manager.set_idle.assert_called_once_with("main")


def test_worker_loop_releases_runner_when_no_work():
    mock_db = MagicMock()
    mock_db.list_videos.return_value = []
    mock_settings = MagicMock()
    mock_settings.config = {}
    mock_runner = MagicMock()
    mock_pool = MagicMock()
    mock_pool.size.return_value = 1
    mock_pool.acquire.return_value = (0, mock_runner)
    mock_agent_manager = MagicMock()
    mock_control = MagicMock()
    mock_control.is_paused.return_value = False

    wt = WorkerThread(
        mock_db, mock_settings, mock_pool, mock_agent_manager, mock_control, max_workers=1
    )
    wt.start()
    time.sleep(0.25)
    wt.stop(timeout=1)

    mock_pool.release.assert_called_with(0)
