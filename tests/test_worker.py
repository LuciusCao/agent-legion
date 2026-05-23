import json
import subprocess

from server.app.db import Database
from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.settings import load_settings
from server.app.worker import (
    discover_openclaw_agents,
    init_runners,
    process_next,
    process_video_once,
    recover_interrupted_videos,
)


class TestProvider(TranscriptionProvider):
    name = "sensevoice"

    def transcribe(self, video_path, output_path, title):
        output_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8")


class ChapterRunner:
    def __init__(self):
        self.calls = 0

    def run(self, phase, video_id, video_dir, prompt_dir, log_path):
        self.calls += 1
        (video_dir / "chapters_raw.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        (video_dir / "chapters.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        return type("Result", (), {"status": "completed", "error_message": ""})()


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


def test_worker_resumes_running_video_after_restart(tmp_path):
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
    db.update_video(video["id"], storage_dir=str(video_dir), current_phase="assemble", status="running")

    assert db.recover_running_videos() == 1
    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    assert db.get_video(video["id"])["status"] == "completed"


def test_recovered_agent_phase_clears_partial_outputs_before_rerun(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
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


def test_discover_openclaw_agents_uses_cli_json(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        assert command == ["openclaw", "agents", "list", "--json"]
        assert capture_output is True
        assert text is True
        assert timeout == 10
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"id": "main"}, {"id": "agent_1"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert discover_openclaw_agents() == ["main", "agent_1"]


def test_init_runners_returns_explicit_runner_count(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"]["runners"] = [
        {"command_template": ["openclaw", "agent", "--agent", "main"]},
        {"command_template": ["openclaw", "agent", "--agent", "agent_1"]},
    ]

    assert init_runners(settings) == 2


def test_question_video_skips_interaction_and_content_review(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
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
    db.update_video("question_Q001", storage_dir=str(video_dir), current_phase="chapter_generate", status="queued")

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    updated = db.get_video("question_Q001")
    assert updated["current_phase"] == "assemble"
    assert updated["status"] == "queued"


def test_missing_url_video_is_not_processed(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    assert process_video_once(db, settings, "question_Q001") is False
    assert db.get_video("question_Q001")["status"] == "missing_url"


def test_missing_url_fetch_error_is_visible(tmp_path, monkeypatch):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    db.create_video("", "Knowledge 1", content_type="knowledge", external_id="K001")

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")

    def fail_fetch(code, api_url, token):
        raise RuntimeError("cms timeout")

    monkeypatch.setattr("server.app.worker.fetch_knowledge_url", fail_fetch)

    assert process_video_once(db, settings, "knowledge_K001") is False
    video = db.get_video("knowledge_K001")
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"
    assert "cms timeout" in video["error_message"]


def test_worker_retries_missing_url_video_from_cms(tmp_path, monkeypatch):
    settings = load_settings(data_dir=tmp_path)
    db = Database(settings.data_dir / "app.sqlite")
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.worker.fetch_question_url",
        lambda uuid, api_url, token: "https://example.com/q001.mp4",
    )
    monkeypatch.setattr(
        "server.app.worker.download_video",
        lambda url, output_path: output_path.write_bytes(b"fake"),
    )

    processed = process_video_once(db, settings, "question_Q001")

    video = db.get_video("question_Q001")
    assert processed is True
    assert video["source_url"] == "https://example.com/q001.mp4"
    assert video["current_phase"] == "transcribe"


def test_process_next_continues_after_unresolved_missing_url(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["cms"] = {}
    db = Database(settings.data_dir / "app.sqlite")
    video = db.create_video("https://example.com/a.mp4", "A")
    db.create_video("", "Question 1", content_type="question", external_id="Q001")
    with db.connect() as conn:
        conn.execute("update videos set created_at='2000-01-01 00:00:00' where id=?", (video["id"],))
        conn.execute(
            "update videos set created_at='2999-01-01 00:00:00' where id=?",
            ("question_Q001",),
        )
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
    db.update_video(video["id"], storage_dir=str(video_dir), current_phase="assemble", status="queued")

    assert process_next(db, settings) is True
    assert db.get_video(video["id"])["status"] == "completed"
