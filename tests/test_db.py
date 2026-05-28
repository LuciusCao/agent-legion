import json
from pathlib import Path

import pytest

from server.app.db import Database
from server.app.db.notifications import NotificationHub
from server.app.records import PHASE_RUN_FIELDS, VIDEO_RECORD_FIELDS


def test_database_creates_video_and_phase_run(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(run["id"], "completed", 0, "")

    videos = db.list_videos()
    runs = db.list_phase_runs(video["id"])

    assert videos[0]["id"] == "a"
    assert videos[0]["status"] == "completed"
    assert runs[0]["phase_key"] == "download"
    assert runs[0]["exit_code"] == 0


def test_database_rows_match_declared_record_fields(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])

    assert set(video) == VIDEO_RECORD_FIELDS
    assert set(db.get_video(video["id"])) == VIDEO_RECORD_FIELDS
    assert set(db.list_videos()[0]) == VIDEO_RECORD_FIELDS
    assert set(run) == PHASE_RUN_FIELDS
    assert set(db.list_phase_runs(video["id"])[0]) == PHASE_RUN_FIELDS


def test_phase_runs_include_openclaw_agent_session_from_command(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(
        video["id"],
        "subtitle_review",
        ["openclaw", "agent", "--agent", "main", "--session-id", "a-123"],
    )

    listed = db.list_phase_runs(video["id"])[0]
    fetched = db.get_phase_run(video["id"], run["id"])

    assert listed["agent_id"] == "main"
    assert listed["agent_session_id"] == "a-123"
    assert fetched["agent_session_id"] == "a-123"


def test_database_update_video_rejects_unknown_fields(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    with pytest.raises(ValueError, match="Unknown video fields"):
        db.update_video(video["id"], status="queued", bad_field="x")

    assert db.get_video(video["id"])["status"] == "queued"


def test_database_update_video_builds_sql_from_whitelist(db):
    """Valid fields are updated; the SQL construction uses only whitelisted keys."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    db.update_video(
        video["id"],
        status="running",
        current_phase="transcribe",
        title="Updated Title",
    )

    updated = db.get_video(video["id"])
    assert updated["status"] == "running"
    assert updated["current_phase"] == "transcribe"
    assert updated["title"] == "Updated Title"
    # source_url should remain unchanged
    assert updated["source_url"] == "https://example.com/path/a.mp4"


def test_notify_includes_interaction_stats_for_knowledge_videos(tmp_path: Path) -> None:
    """Regression test for issue 002: SSE push should include interaction_stats."""
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    video_dir = videos_dir / "knowledge_K001"
    video_dir.mkdir()

    # Create interaction artifacts
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )

    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub, videos_dir=videos_dir)
    db.create_video(
        "https://example.com/k001.mp4",
        title="K001",
        content_type="knowledge",
        external_id="K001",
        storage_dir=str(video_dir),
    )

    # Trigger _notify via update_video
    db.update_video("knowledge_K001", current_phase="assemble", status="completed")

    assert "interaction_stats" in captured
    assert captured["interaction_stats"] == {
        "example_practice": {"passed": 1, "total": 1},
        "interaction_summary": {"passed": 1, "total": 1},
    }
    assert captured.get("interaction_review_status") == "all_passed"


def test_notify_does_not_include_interaction_stats_for_question_videos(tmp_path: Path) -> None:
    """Question videos should not have interaction fields injected into SSE payload."""
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    video_dir = videos_dir / "question_Q001"
    video_dir.mkdir()

    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub, videos_dir=videos_dir)
    db.create_video(
        "https://example.com/q001.mp4",
        title="Q001",
        content_type="question",
        external_id="Q001",
        storage_dir=str(video_dir),
    )

    db.update_video("question_Q001", current_phase="assemble", status="completed")

    assert "interaction_stats" not in captured
    assert "interaction_review_status" not in captured


def test_notify_skips_interaction_stats_when_videos_dir_is_none(tmp_path: Path) -> None:
    """When videos_dir is not provided, _notify should not crash and skip enrichment."""
    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub)
    db.create_video(
        "https://example.com/k001.mp4",
        title="K001",
        content_type="knowledge",
        external_id="K001",
    )

    db.update_video("knowledge_K001", current_phase="assemble", status="completed")

    assert "interaction_stats" not in captured
    assert "interaction_review_status" not in captured


def test_update_video_rejects_completed_without_assemble_phase(db):
    """Regression test for issue 003: status='completed' requires current_phase='assemble'."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    # Setting status='completed' while current_phase='download' should fail
    with pytest.raises(ValueError, match="Invalid state: status='completed' requires current_phase='assemble'",
    ):
        db.update_video(video["id"], status="completed")

    # Setting both to valid combination should succeed
    db.update_video(video["id"], current_phase="assemble", status="completed")
    updated = db.get_video(video["id"])
    assert updated["status"] == "completed"
    assert updated["current_phase"] == "assemble"

    # Transitioning from completed back to queued should succeed
    db.update_video(video["id"], current_phase="download", status="queued")
    updated = db.get_video(video["id"])
    assert updated["status"] == "queued"
    assert updated["current_phase"] == "download"

    # Setting current_phase to download while status is already queued should succeed
    db.update_video(video["id"], current_phase="download")
    assert db.get_video(video["id"])["status"] == "queued"


def test_update_video_allows_completed_with_assemble_from_any_phase(db):
    """Valid transition: any phase can become completed+assemble."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(1, "completed", 0, "")

    # Transition to completed+assemble should succeed
    db.update_video(video["id"], current_phase="assemble", status="completed")
    updated = db.get_video(video["id"])
    assert updated["status"] == "completed"
    assert updated["current_phase"] == "assemble"
