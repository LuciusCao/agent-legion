import pytest

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
