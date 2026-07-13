from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from server.app.worker import (
    WorkerCapacity,
    pick_next_work,
    process_next,
    process_video_once,
)
from tests.helpers import TestProvider


def test_missing_url_video_is_not_processed(db, settings):
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    assert process_video_once(db, settings, "question_Q001") is False
    assert db.get_video("question_Q001")["status"] == "missing_url"


def test_missing_url_fetch_error_is_visible(db, settings, monkeypatch):
    db.create_video("", "Knowledge 1", content_type="knowledge", external_id="K001")

    monkeypatch.setattr(
        "server.app.services.video_execution.get_token", lambda env, config: "token"
    )

    def fail_fetch(code, api_url, token):
        raise RuntimeError("cms timeout")

    monkeypatch.setattr("server.app.services.video_execution.lookup_knowledge_video", fail_fetch)

    assert process_video_once(db, settings, "knowledge_K001") is False
    video = db.get_video("knowledge_K001")
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"
    assert "cms timeout" in video["error_message"]


def test_worker_retries_missing_url_video_from_cms(db, settings, monkeypatch):
    db.create_video("", "Question 1", content_type="question", external_id="Q001")

    monkeypatch.setattr(
        "server.app.services.video_execution.get_token", lambda env, config: "token"
    )
    monkeypatch.setattr(
        "server.app.services.video_execution.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup",
            (),
            {
                "status": "found",
                "url": "https://example.com/q001.mp4",
                "title": "Question 1",
                "source_uuid": "uuid-q001",
            },
        )(),
    )
    monkeypatch.setattr(
        "server.app.services.video_execution.download_video",
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
        conn.execute(
            "update videos set created_at='2000-01-01 00:00:00' where id=?", (video["id"],)
        )
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
    db.update_video(
        video["id"], storage_dir=str(video_dir), current_phase="assemble", status="queued"
    )

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


def test_process_next_uses_bounded_polling_query(db, settings):
    db.create_video("https://example.com/a.mp4", "A")
    db.update_video("a", status="queued", current_phase="download")

    with patch.object(db, "list_videos") as mock_list:
        mock_list.return_value = []
        process_next(db, settings)
        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args.kwargs
        assert call_kwargs["limit"] == 100
        assert call_kwargs.get("status_filter") == ["queued", "missing_url", "running"]


def test_process_next_scans_later_pages_without_starvation(db, settings):
    settings.config.setdefault("worker", {})["poll_batch_size"] = 1
    blocked = db.create_video("https://example.com/blocked.mp4", "Blocked")
    ready = db.create_video("https://example.com/ready.mp4", "Ready")
    db.update_video(blocked["id"], current_phase="subtitle_review", status="queued")
    with db.connect() as conn:
        conn.execute(
            "update videos set created_at='2026-01-02 00:00:00' where id=?", (blocked["id"],)
        )
        conn.execute(
            "update videos set created_at='2026-01-01 00:00:00' where id=?", (ready["id"],)
        )

    with patch("server.app.worker.process_video_once", return_value=True) as process_once:
        assert process_next(db, settings) is True

    process_once.assert_called_once()
    assert process_once.call_args.args[2] == ready["id"]


def test_phase_run_log_path_is_persisted_relative(db, settings):
    video = db.create_video("https://example.com/a.mp4", "A")
    video_dir = settings.videos_dir / "a"
    video_dir.mkdir(parents=True)
    (video_dir / "a.mp4").write_bytes(b"fake")
    db.update_video("a", storage_dir=str(video_dir), current_phase="transcribe", status="queued")

    process_video_once(db, settings, video["id"], providers=[TestProvider()])

    runs = db.list_phase_runs("a")
    assert runs
    assert runs[-1]["log_path"].startswith("logs/")
    assert not Path(runs[-1]["log_path"]).is_absolute()


def test_agent_phase_runner_receives_absolute_log_path(db, settings):
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
    runner = _RecordingChapterRunner()

    process_video_once(db, settings, video["id"], openclaw_runner=runner)

    assert runner.calls == 1
    assert runner.log_path is not None
    assert runner.log_path.is_absolute()
    assert runner.log_path == settings.logs_dir / f"{video['id']}-chapter_generate.log"


class _RecordingChapterRunner:
    def __init__(self):
        self.calls = 0
        self.log_path: Path | None = None

    def run(self, phase, video_id: str, video_dir: Path, prompt_dir: Path, log_path: Path):
        self.calls += 1
        self.log_path = log_path
        phase_key = getattr(phase, "key", str(phase))
        (video_dir / "chapters_raw.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        (video_dir / "chapters.json").write_text(
            json.dumps([{"id": "C1", "start_time": 0, "end_time": 2, "title": "开始"}]),
            encoding="utf-8",
        )
        return type(
            "Result",
            (),
            {
                "status": "completed",
                "error_message": "",
                "command": ["openclaw", phase_key, video_id],
            },
        )()
