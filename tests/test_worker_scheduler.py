from pathlib import Path

from server.app.settings import load_settings
from server.app.worker_scheduler import (
    WorkerCapacity,
    get_phase_concurrency_limit,
    pick_next_work,
)


def test_get_phase_concurrency_limit_invalid_string_returns_default(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["worker"] = {"phase_concurrency": {"download": "invalid"}}
    result = get_phase_concurrency_limit(settings, "download")
    assert result == 10  # DEFAULT_PHASE_CONCURRENCY["download"]


def test_get_phase_concurrency_limit_none_returns_default(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["worker"] = {"phase_concurrency": {"download": None}}
    result = get_phase_concurrency_limit(settings, "download")
    assert result == 10


def test_pick_next_work_skips_non_queued_videos():
    videos = [
        {"id": "v1", "status": "completed", "current_phase": "download"},
        {"id": "v2", "status": "failed", "current_phase": "download"},
    ]
    capacity = WorkerCapacity(free_runner=(0, None), running_local_counts={})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, set(), capacity, settings)
    assert result is None


def test_pick_next_work_skips_already_running():
    videos = [
        {"id": "v1", "status": "queued", "current_phase": "download"},
    ]
    capacity = WorkerCapacity(free_runner=(0, None), running_local_counts={})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, {"v1"}, capacity, settings)
    assert result is None


def test_pick_next_work_local_phase_at_limit():
    videos = [
        {"id": "v1", "status": "queued", "current_phase": "download"},
    ]
    capacity = WorkerCapacity(free_runner=None, running_local_counts={"download": 10})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, set(), capacity, settings)
    assert result is None


def test_pick_next_work_missing_url_status_is_considered():
    videos = [
        {"id": "v1", "status": "missing_url", "current_phase": "waiting_for_url"},
    ]
    capacity = WorkerCapacity(free_runner=None, running_local_counts={})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, set(), capacity, settings)
    assert result is not None
    assert result.video["id"] == "v1"


def test_pick_next_work_agent_phase_with_free_runner():
    videos = [
        {"id": "v1", "status": "queued", "current_phase": "subtitle_review"},
    ]
    capacity = WorkerCapacity(free_runner=(0, None), running_local_counts={})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, set(), capacity, settings)
    assert result is not None
    assert result.video["id"] == "v1"
    assert result.kind == "agent"


def test_pick_next_work_agent_phase_no_free_runner():
    videos = [
        {"id": "v1", "status": "queued", "current_phase": "subtitle_review"},
    ]
    capacity = WorkerCapacity(free_runner=None, running_local_counts={})
    settings = load_settings(data_dir=Path("/tmp"))
    result = pick_next_work(videos, set(), capacity, settings)
    assert result is None
