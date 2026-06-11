from __future__ import annotations

import pytest

from server.app.agents import AgentStatusManager
from server.app.pipeline.runners import RunnerPool
from server.app.worker_control import WorkerControl
from server.app.worker_thread import WorkerThread
from tests.helpers import wait_for_predicate


@pytest.fixture
def agent_manager():
    return AgentStatusManager()


@pytest.fixture
def runner_pool(agent_manager):
    return RunnerPool([], agent_manager=agent_manager)


@pytest.fixture
def worker_control():
    return WorkerControl()


@pytest.fixture
def worker_thread(db, settings, runner_pool, agent_manager, worker_control):
    wt = WorkerThread(db, settings, runner_pool, agent_manager, worker_control)
    yield wt
    wt.stop(timeout=3)


def test_worker_advances_download_to_transcribe(db, worker_thread, worker_control, monkeypatch):
    """Worker loop drives a real video record through the download phase."""
    import server.app.worker as worker_module

    def noop_download(ctx):
        # No actual download is needed for this integration test.
        pass

    monkeypatch.setitem(worker_module._default_registry._handlers, "download", noop_download)

    video = db.create_video(
        "https://example.com/v1.mp4",
        title="Integration Test",
        content_type="knowledge",
        external_id="K001",
    )
    assert video["current_phase"] == "download"
    assert video["status"] == "queued"

    worker_thread.start()
    worker_control.resume()
    worker_control.request_tick()

    def _reached_transcribe():
        v = db.get_video(video["id"])
        return v is not None and v["current_phase"] == "transcribe" and v["status"] == "queued"

    wait_for_predicate(_reached_transcribe, timeout=5.0)
