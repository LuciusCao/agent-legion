import json
import subprocess
from pathlib import Path

from server.app.pipeline.recovery import recover_interrupted_videos
from server.app.pipeline.runners import RunnerPool, discover_openclaw_agents
from server.app.settings import load_settings
from server.app.worker import (
    WorkerCapacity,
    get_phase_concurrency_limit,
    pick_next_work,
    process_next,
    process_video_once,
)
from tests.conftest import ChapterRunner, TestProvider


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
    db.update_video(video["id"], storage_dir=str(video_dir), current_phase="assemble", status="running")

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


def test_runner_pool_from_settings_returns_explicit_runner_count(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"]["runners"] = [
        {"command_template": ["openclaw", "agent", "--agent", "main"]},
        {"command_template": ["openclaw", "agent", "--agent", "agent_1"]},
    ]

    pool = RunnerPool.from_settings(settings)
    assert pool.size() == 2


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
    db.update_video("question_Q001", storage_dir=str(video_dir), current_phase="chapter_generate", status="queued")

    processed = process_video_once(db, settings, video["id"])

    assert processed is True
    updated = db.get_video("question_Q001")
    assert updated["current_phase"] == "assemble"
    assert updated["status"] == "queued"


def test_missing_url_video_is_not_processed(db, settings):
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    assert process_video_once(db, settings, "question_Q001") is False
    assert db.get_video("question_Q001")["status"] == "missing_url"


def test_missing_url_fetch_error_is_visible(db, settings, monkeypatch):
    db.create_video("", "Knowledge 1", content_type="knowledge", external_id="K001")

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")

    def fail_fetch(code, api_url, token):
        raise RuntimeError("cms timeout")

    monkeypatch.setattr("server.app.worker.lookup_knowledge_video", fail_fetch)

    assert process_video_once(db, settings, "knowledge_K001") is False
    video = db.get_video("knowledge_K001")
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"
    assert "cms timeout" in video["error_message"]


def test_worker_retries_missing_url_video_from_cms(db, settings, monkeypatch):
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.worker.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup", (), {"status": "found", "url": "https://example.com/q001.mp4", "title": "Question 1", "source_uuid": "uuid-q001"}
        )(),
    )
    monkeypatch.setattr(
        "server.app.worker.download_video",
        lambda url, output_path: output_path.write_bytes(b"fake"),
    )

    processed = process_video_once(db, settings, "question_Q001")

    video = db.get_video("question_Q001")
    assert processed is True
    assert video["source_url"] == "https://example.com/q001.mp4"
    assert video["source_uuid"] == "uuid-q001"
    assert video["current_phase"] == "transcribe"


def test_process_next_continues_after_unresolved_missing_url(db, settings):
    settings.config["cms"] = {}
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


def test_local_phase_can_be_scheduled_without_openclaw_runner(db, settings):
    download_video = db.create_video("https://example.com/a.mp4", "A")
    db.create_video("https://example.com/b.mp4", "B")
    db.update_video("b", current_phase="subtitle_review", status="queued")

    work = pick_next_work(
        db.list_videos(),
        running_video_ids=set(),
        capacity=WorkerCapacity(free_runner=None, running_local_counts={}),
        settings=settings,
    )

    assert work is not None
    assert work.video["id"] == download_video["id"]
    assert work.kind == "local"


def test_process_next_skips_agent_phase_without_runner(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8"
    )
    db.update_video(
        video["id"],
        storage_dir=str(video_dir),
        current_phase="subtitle_review",
        status="queued",
    )

    assert process_next(db, settings) is False
    assert db.get_video(video["id"])["status"] == "queued"


def test_transcribe_concurrency_limit_is_configurable(settings):
    settings.config["worker"] = {"phase_concurrency": {"transcribe": 3}}

    assert get_phase_concurrency_limit(settings, "download") == 10
    assert get_phase_concurrency_limit(settings, "transcribe") == 3
