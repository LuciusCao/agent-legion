import time
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from server.app.worker_control import WorkerControl
from server.app.worker_thread import WorkerThread


def test_worker_thread_start_and_stop():
    """WorkerThread can be started and stopped cleanly."""
    db = MagicMock()
    db.list_videos.return_value = []
    settings = MagicMock()
    settings.config = {}
    runner_pool = MagicMock()
    runner_pool.size.return_value = 1
    agent_manager = MagicMock()
    worker_control = WorkerControl()  # 默认 paused=True

    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)

    wt.start()
    assert wt.executor is not None
    assert wt._thread is not None
    assert wt._thread.is_alive()

    time.sleep(0.3)

    wt.stop(timeout=2)
    assert not wt._thread.is_alive()


def test_worker_thread_agent_done_callback_cleans_state():
    """Agent work done callback removes video from running_futures and releases runner."""
    db = MagicMock()
    settings = MagicMock()
    settings.config = {}
    runner_pool = MagicMock()
    runner_pool.size.return_value = 1
    agent_manager = MagicMock()
    worker_control = WorkerControl()

    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)
    wt.start()

    future = Future()
    wt.running_futures["v1"] = future

    def _finish_agent_work(video_id: str, runner_index: int, agent_id: str) -> None:
        with wt.running_lock:
            wt.running_futures.pop(video_id, None)
        wt.runner_pool.release(runner_index)
        wt.agent_manager.set_idle(agent_id)

    future.add_done_callback(lambda _f: _finish_agent_work("v1", 0, "agent-1"))
    future.set_result(True)

    assert "v1" not in wt.running_futures
    wt.runner_pool.release.assert_called_once_with(0)
    wt.agent_manager.set_idle.assert_called_once_with("agent-1")

    wt.stop(timeout=2)


def test_worker_thread_local_done_callback_cleans_counts():
    """Local work done callback removes video and decrements phase count."""
    db = MagicMock()
    settings = MagicMock()
    settings.config = {}
    runner_pool = MagicMock()
    runner_pool.size.return_value = 1
    agent_manager = MagicMock()
    worker_control = WorkerControl()

    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)
    wt.start()

    future = Future()
    wt.running_futures["v1"] = future
    wt.running_local_counts["download"] = 1

    def _finish_local_work(video_id: str, phase: str) -> None:
        with wt.running_lock:
            wt.running_futures.pop(video_id, None)
            next_count = wt.running_local_counts.get(phase, 0) - 1
            if next_count > 0:
                wt.running_local_counts[phase] = next_count
            else:
                wt.running_local_counts.pop(phase, None)

    future.add_done_callback(lambda _f: _finish_local_work("v1", "download"))
    future.set_result(True)

    assert "v1" not in wt.running_futures
    assert "download" not in wt.running_local_counts

    wt.stop(timeout=2)


def test_worker_thread_executes_local_work():
    """Worker loop submits local work and cleans up on completion."""
    db = MagicMock()
    db.list_videos.return_value = [{"id": "v1", "status": "queued", "current_phase": "download"}]
    settings = MagicMock()
    settings.config = {}
    runner_pool = MagicMock()
    runner_pool.size.return_value = 1
    runner_pool.acquire.return_value = None
    agent_manager = MagicMock()
    worker_control = WorkerControl()
    worker_control.resume()

    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)

    with patch("server.app.worker.process_video_once", return_value=True):
        wt.start()
        time.sleep(0.6)

    wt.stop(timeout=2)
    assert not wt._thread.is_alive()


def test_worker_thread_executes_agent_work():
    """Worker loop submits agent work and cleans up on completion."""
    db = MagicMock()
    db.list_videos.return_value = [
        {"id": "v1", "status": "queued", "current_phase": "subtitle_review"}
    ]
    settings = MagicMock()
    settings.config = {}
    runner = MagicMock()
    runner.agent_id = "agent-1"
    runner_pool = MagicMock()
    runner_pool.size.return_value = 1
    runner_pool.acquire.return_value = (0, runner)
    agent_manager = MagicMock()
    worker_control = WorkerControl()
    worker_control.resume()

    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)

    with patch("server.app.worker.process_video_once", return_value=True):
        wt.start()
        time.sleep(0.6)

    wt.stop(timeout=2)
    assert not wt._thread.is_alive()
