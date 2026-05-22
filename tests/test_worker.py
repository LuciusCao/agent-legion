import json

from server.app.db import Database
from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.settings import load_settings
from server.app.worker import process_video_once


class TestProvider(TranscriptionProvider):
    name = "sensevoice"

    def transcribe(self, video_path, output_path, title):
        output_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8")


def test_worker_processes_transcribe_phase(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake")
    db.update_video("a", storage_dir=str(video_dir), current_phase="transcribe", status="queued")

    processed = process_video_once(db, settings, video["id"], providers=[TestProvider()])

    assert processed is True
    assert (video_dir / "subtitles.srt").exists()
    assert db.get_video("a")["current_phase"] == "subtitle_review"


def test_worker_processes_assemble_phase(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
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


def test_question_video_skips_interaction_and_content_review(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    video = db.create_video(
        "https://example.com/q1.mp4",
        "Q1",
        content_type="question",
        external_id="Q001",
    )
    video_dir = settings.videos_dir / "q1"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n题目讲解\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "解析", "concepts": []}]),
        encoding="utf-8",
    )
    db.update_video("q1", storage_dir=str(video_dir), current_phase="chapter_generate", status="queued")

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    updated = db.get_video("q1")
    assert updated["current_phase"] == "assemble"
    assert updated["status"] == "queued"


def test_missing_url_video_is_not_processed(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    assert process_video_once(db, settings, "question_Q001") is False
    assert db.get_video("question_Q001")["status"] == "missing_url"
