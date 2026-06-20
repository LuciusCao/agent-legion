from __future__ import annotations

import json
from pathlib import Path

from server.app.worker import (
    process_video_once,
)
from tests.helpers import TestProvider


def test_worker_processes_transcribe_phase(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake")
    db.update_video("a", storage_dir=str(video_dir), current_phase="transcribe", status="queued")

    processed = process_video_once(db, settings, video["id"], providers=[TestProvider()])

    assert processed is True
    assert (video_dir / "subtitles.srt").exists()
    assert db.get_video("a")["current_phase"] == "subtitle_review"
    transcription_runs = db.list_transcription_runs("a")
    assert len(transcription_runs) == 1
    assert transcription_runs[0]["provider"] == "sensevoice"
    assert transcription_runs[0]["status"] == "completed"
    assert transcription_runs[0]["srt_entry_count"] == 1


def test_worker_processes_assemble_phase(db, settings):
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
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert (video_dir / "metadata.json").exists()
    assert db.get_video("a")["status"] == "completed"


def test_worker_deletes_mp4_after_assemble_when_config_enabled(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake mp4")
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
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    settings.config["cleanup_video_after_assemble"] = True
    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert not (video_dir / "a.mp4").exists()
    assert (video_dir / "metadata.json").exists()


def test_worker_keeps_mp4_after_assemble_when_config_disabled(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake mp4")
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
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    settings.config["cleanup_video_after_assemble"] = False
    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert (video_dir / "a.mp4").exists()
    assert (video_dir / "metadata.json").exists()


def test_worker_appends_cleanup_warning_to_log(db, settings, monkeypatch):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake mp4")
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
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    settings.config["cleanup_video_after_assemble"] = True

    def raise_permission_error(self):
        raise PermissionError("simulated cleanup failure")

    monkeypatch.setattr(Path, "unlink", raise_permission_error)

    # Pre-create log file so append has something to append to
    log_path = settings.logs_dir / "a-assemble.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("existing content", encoding="utf-8")

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    log_text = log_path.read_text(encoding="utf-8")
    assert "existing content" in log_text
    assert "Cleanup warning" in log_text


def test_worker_assemble_succeeds_even_when_cleanup_fails(db, settings, monkeypatch):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake mp4")
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
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    settings.config["cleanup_video_after_assemble"] = True

    def raise_permission_error(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", raise_permission_error)

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert db.get_video("a")["status"] == "completed"
    assert (video_dir / "metadata.json").exists()


def test_question_video_skips_interaction_and_content_review(db, settings):
    video = db.create_video(
        "https://example.com/q1.mp4",
        "Q1",
        content_type="question",
        external_id="Q001",
    )
    video_dir = settings.videos_dir / "question_Q001"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n题目讲解\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "解析", "concepts": []}]),
        encoding="utf-8",
    )
    db.update_video(
        "question_Q001",
        storage_dir=str(video_dir),
        current_phase="chapter_generate",
        status="queued",
    )

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    updated = db.get_video("question_Q001")
    assert updated["current_phase"] == "assemble"
    assert updated["status"] == "queued"


def test_process_video_once_stops_after_target_phase(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake")
    db.update_video("a", storage_dir=str(video_dir), current_phase="transcribe", status="queued")

    processed = process_video_once(
        db,
        settings,
        video["id"],
        providers=[TestProvider()],
        stop_after_phase="transcribe",
    )

    assert processed is True
    updated = db.get_video("a")
    assert updated["current_phase"] == "subtitle_review"
    assert updated["status"] == "queued"
    phase_run = db.list_phase_runs("a")[-1]
    assert phase_run["phase_key"] == "transcribe"
    assert phase_run["status"] == "completed"


def test_process_video_once_marks_completed_when_target_is_final_phase(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n",
        encoding="utf-8",
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
        encoding="utf-8",
    )
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}),
        encoding="utf-8",
    )
    db.update_video("a", storage_dir=str(video_dir), current_phase="assemble", status="queued")

    processed = process_video_once(db, settings, video["id"], stop_after_phase="assemble")

    assert processed is True
    assert db.get_video("a")["status"] == "completed"
