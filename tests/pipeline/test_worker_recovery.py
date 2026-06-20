from __future__ import annotations

import json

from server.app.pipeline.recovery import recover_interrupted_videos
from server.app.worker import (
    process_video_once,
)
from tests.helpers import ChapterRunner


def test_worker_resumes_running_video_after_restart(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始", "concepts": []}]),
        encoding="utf-8",
    )
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )
    db.update_video(
        video["id"], storage_dir=str(video_dir), current_phase="assemble", status="running"
    )

    assert db.recover_running_videos() == 1
    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert db.get_video(video["id"])["status"] == "completed"


def test_recovered_agent_phase_clears_partial_outputs_before_rerun(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text("{bad json", encoding="utf-8")
    db.update_video(
        video["id"],
        storage_dir=str(video_dir),
        current_phase="chapter_generate",
        status="running",
    )
    runner = ChapterRunner()

    assert recover_interrupted_videos(db, settings) == 1
    processed = process_video_once(db, settings, video["id"], openclaw_runner=runner)

    assert processed is True
    assert runner.calls == 1
    assert db.get_video(video["id"])["current_phase"] == "interaction_generate"


def test_agent_phase_records_rendered_command(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8"
    )
    db.update_video(
        video["id"],
        storage_dir=str(video_dir),
        current_phase="chapter_generate",
        status="queued",
    )
    runner = ChapterRunner()

    assert process_video_once(db, settings, video["id"], openclaw_runner=runner) is True

    runs = db.list_phase_runs(video["id"])
    assert json.loads(runs[-1]["command_json"]) != []


def test_existing_invalid_agent_output_fails_instead_of_advancing(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "chapters.json").write_text("{bad json", encoding="utf-8")
    db.update_video(
        video["id"],
        storage_dir=str(video_dir),
        current_phase="chapter_generate",
        status="queued",
    )

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    updated = db.get_video(video["id"])
    assert updated["status"] == "failed"
    assert updated["current_phase"] == "chapter_generate"
