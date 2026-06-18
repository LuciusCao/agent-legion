import json

from server.app.services.intake import add_video_items, normalized_content_type
from server.app.services.manual_run import batch_run_to_phase, run_to_phase
from server.app.services.video_actions import (
    batch_rerun_video_records,
    delete_video_record,
    rerun_video_record,
    select_videos_for_package,
)
from tests.helpers import InputItem, TestProvider


def test_intake_normalizes_unknown_content_type_and_creates_storage(db, settings):
    result = add_video_items(
        db,
        settings,
        [InputItem(url="https://example.com/course/g1.mp4", content_type="unknown")],
    )

    video = result["videos"][0]
    assert normalized_content_type("unknown") == "knowledge"
    assert video["id"] == "g1"
    assert video["content_type"] == "knowledge"
    assert (settings.data_dir / "videos" / "g1").is_dir()


def test_batch_rerun_uses_same_normalization_as_single_rerun(db, settings):
    db.create_video("https://example.com/q1.mp4", content_type="question", external_id="Q001")
    db.update_video("question_Q001", status="completed", current_phase="assemble")

    results = batch_rerun_video_records(
        db,
        settings,
        ["question_Q001", "missing"],
        "content_review",
    )

    assert results == [
        {"video_id": "question_Q001", "status": "rerun", "phase": "assemble", "message": ""},
        {
            "video_id": "missing",
            "status": "not_found",
            "phase": "content_review",
            "message": "Video not found",
        },
    ]
    assert db.get_video("question_Q001")["current_phase"] == "assemble"


def test_rerun_rejects_running_video(db, settings):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.update_video("knowledge_K001", status="running", current_phase="chapter_generate")

    result = rerun_video_record(db, settings, "knowledge_K001", "subtitle_review")

    assert result["status"] == "busy"
    assert db.get_video("knowledge_K001")["current_phase"] == "chapter_generate"
    assert db.get_video("knowledge_K001")["status"] == "running"


def test_rerun_rejects_video_with_running_phase_run_even_if_video_is_queued(db, settings):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    run = db.start_phase("knowledge_K001", "chapter_generate", [])
    assert run is not None
    db.update_video("knowledge_K001", status="queued", current_phase="subtitle_review")

    result = rerun_video_record(db, settings, "knowledge_K001", "subtitle_review")

    assert result["status"] == "busy"
    assert db.get_video("knowledge_K001")["current_phase"] == "subtitle_review"
    assert db.get_video("knowledge_K001")["status"] == "queued"


def test_delete_video_record_removes_storage_and_package_selection_defaults(db, settings):
    completed = db.create_video(
        "https://example.com/k1.mp4",
        content_type="knowledge",
        external_id="K001",
    )
    queued = db.create_video(
        "https://example.com/k2.mp4",
        content_type="knowledge",
        external_id="K002",
    )
    db.update_video(completed["id"], status="completed", current_phase="assemble")
    storage_dir = settings.data_dir / "videos" / completed["id"]
    storage_dir.mkdir(parents=True)
    db.update_video(completed["id"], storage_dir=str(storage_dir))

    default_selection = select_videos_for_package(db)
    explicit_selection = select_videos_for_package(db, [queued["id"], "missing"])

    assert [video["id"] for video in default_selection.videos] == [completed["id"]]
    assert default_selection.missing_ids == []
    assert [video["id"] for video in explicit_selection.videos] == []
    assert explicit_selection.missing_ids == ["missing"]
    assert explicit_selection.incomplete_ids == [queued["id"]]
    assert delete_video_record(db, settings, completed["id"]) is True
    assert db.get_video(completed["id"]) is None
    assert not storage_dir.exists()


def test_rerun_transcribe_downgrades_to_download_when_mp4_missing(db, settings):
    db.create_video("https://example.com/v1.mp4", "V1")
    video_dir = settings.videos_dir / "v1"
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
    )
    db.update_video("v1", storage_dir=str(video_dir), status="completed", current_phase="assemble")

    result = rerun_video_record(db, settings, "v1", "transcribe")

    assert result["status"] == "rerun"
    assert result["phase"] == "download"
    assert db.get_video("v1")["current_phase"] == "download"


def test_rerun_transcribe_stays_transcribe_when_mp4_exists(db, settings):
    db.create_video("https://example.com/v1.mp4", "V1")
    video_dir = settings.videos_dir / "v1"
    video_dir.mkdir(parents=True)
    (video_dir / "v1.mp4").write_bytes(b"fake")
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
    )
    db.update_video("v1", storage_dir=str(video_dir), status="completed", current_phase="assemble")

    result = rerun_video_record(db, settings, "v1", "transcribe")

    assert result["status"] == "rerun"
    assert result["phase"] == "transcribe"
    assert db.get_video("v1")["current_phase"] == "transcribe"


def test_run_to_phase_continues_to_target_and_stops(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    video_dir = settings.videos_dir / video["id"]
    video_dir.mkdir(parents=True)
    (video_dir / f"{video['id']}.mp4").write_bytes(b"fake")
    db.update_video(
        video["id"], storage_dir=str(video_dir), current_phase="transcribe", status="queued"
    )

    result = run_to_phase(
        db,
        settings,
        video["id"],
        target_phase="transcribe",
        providers=[TestProvider()],
    )

    assert result == {
        "video_id": video["id"],
        "status": "run_to",
        "phase": "transcribe",
        "message": "",
    }
    updated = db.get_video(video["id"])
    assert updated["current_phase"] == "subtitle_review"
    assert updated["status"] == "queued"


def test_run_to_phase_fetches_missing_url_before_running_to_target(db, settings, monkeypatch):
    video = db.create_video("", content_type="knowledge", external_id="K001")
    settings.config["cms"] = {"env": "test", "knowledge_url": "https://cms.example/knowledge"}

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.worker.lookup_knowledge_video",
        lambda code, api_url, token: type(
            "Lookup",
            (),
            {
                "status": "found",
                "url": "https://example.com/k001.mp4",
                "title": "Knowledge 1",
                "source_uuid": "uuid-k001",
            },
        )(),
    )
    monkeypatch.setattr(
        "server.app.worker.download_video",
        lambda url, output_path: output_path.write_bytes(b"fake"),
    )

    result = run_to_phase(db, settings, video["id"], target_phase="download")

    video_dir = settings.videos_dir / video["id"]
    assert result["status"] == "run_to"
    updated = db.get_video(video["id"])
    assert updated["source_url"] == "https://example.com/k001.mp4"
    assert updated["source_uuid"] == "uuid-k001"
    assert updated["current_phase"] == "transcribe"
    assert updated["status"] == "queued"
    assert (video_dir / f"{video['id']}.mp4").exists()


def test_run_to_phase_reports_unresolved_missing_url(db, settings, monkeypatch):
    video = db.create_video("", content_type="knowledge", external_id="K001")
    settings.config["cms"] = {"env": "test", "knowledge_url": "https://cms.example/knowledge"}

    monkeypatch.setattr("server.app.worker.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.worker.lookup_knowledge_video",
        lambda code, api_url, token: type(
            "Lookup",
            (),
            {"status": "missing_url", "url": "", "title": "Knowledge 1", "source_uuid": ""},
        )(),
    )

    result = run_to_phase(db, settings, video["id"], target_phase="download")

    assert result["status"] in {"failed", "skipped"}
    assert result["status"] != "run_to"
    assert result["message"]


def test_run_to_phase_returns_busy_when_worker_wins_race(db, settings, monkeypatch):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    db.update_video(video["id"], current_phase="transcribe", status="queued")

    def start_elsewhere(
        db, settings, video_id, providers=None, openclaw_runner=None, stop_after_phase=None
    ):
        db.update_video(video_id, status="running")
        return False

    monkeypatch.setattr("server.app.services.manual_run.process_video_once", start_elsewhere)

    result = run_to_phase(db, settings, video["id"], target_phase="transcribe")

    assert result == {
        "video_id": video["id"],
        "status": "busy",
        "phase": "transcribe",
        "message": "Video is currently being processed",
    }


def test_run_to_phase_rejects_continue_to_earlier_target(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    db.update_video(video["id"], current_phase="chapter_generate", status="queued")

    result = run_to_phase(db, settings, video["id"], target_phase="transcribe")

    assert result["status"] == "invalid_phase"
    assert "目标阶段早于当前阶段" in result["message"]


def test_run_to_phase_reruns_from_start_to_target(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    video_dir = settings.videos_dir / video["id"]
    video_dir.mkdir(parents=True)
    (video_dir / f"{video['id']}.mp4").write_bytes(b"fake")
    (video_dir / "subtitles.srt").write_text("old", encoding="utf-8")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    db.update_video(
        video["id"], storage_dir=str(video_dir), current_phase="assemble", status="queued"
    )

    result = run_to_phase(
        db,
        settings,
        video["id"],
        target_phase="transcribe",
        start_phase="transcribe",
        providers=[TestProvider()],
    )

    assert result["status"] == "rerun_to"
    assert (video_dir / "subtitles.srt").read_text(encoding="utf-8").startswith("1\n")
    assert not (video_dir / "chapters.json").exists()
    assert db.get_video(video["id"])["current_phase"] == "subtitle_review"


def test_batch_run_to_phase_processes_only_requested_ids(db, settings):
    requested = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    unrelated = db.create_video(
        "https://example.com/k2.mp4", content_type="knowledge", external_id="K002"
    )
    for video in [requested, unrelated]:
        video_dir = settings.videos_dir / video["id"]
        video_dir.mkdir(parents=True)
        (video_dir / f"{video['id']}.mp4").write_bytes(b"fake")
        db.update_video(
            video["id"], storage_dir=str(video_dir), current_phase="transcribe", status="queued"
        )

    results = batch_run_to_phase(
        db,
        settings,
        [requested["id"]],
        target_phase="transcribe",
        providers=[TestProvider()],
    )

    assert [result["video_id"] for result in results] == [requested["id"]]
    assert db.get_video(requested["id"])["current_phase"] == "subtitle_review"
    assert db.get_video(unrelated["id"])["current_phase"] == "transcribe"


def test_batch_run_to_phase_skips_invalid_phase_for_mixed_content_types(db, settings):
    knowledge = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    question = db.create_video(
        "https://example.com/q1.mp4", content_type="question", external_id="Q001"
    )
    db.update_video(knowledge["id"], current_phase="assemble", status="completed")

    results = batch_run_to_phase(
        db,
        settings,
        [knowledge["id"], question["id"]],
        target_phase="interaction_generate",
    )

    assert results[0]["video_id"] == knowledge["id"]
    assert results[0]["status"] != "skipped"
    assert results[1]["video_id"] == question["id"]
    assert results[1]["status"] == "skipped"
    assert "不适用于该视频类型" in results[1]["message"]


def test_run_to_phase_question_rejects_knowledge_only_target(db, settings):
    video = db.create_video(
        "https://example.com/q1.mp4", content_type="question", external_id="Q001"
    )

    result = run_to_phase(db, settings, video["id"], target_phase="interaction_generate")

    assert result["status"] == "invalid_phase"
    assert "不适用于该视频类型" in result["message"]


def test_run_to_phase_not_found(db, settings):
    result = run_to_phase(db, settings, "missing", target_phase="download")
    assert result["status"] == "not_found"


def test_run_to_phase_busy(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    db.start_phase(video["id"], "download", ["cmd"])
    db.update_video(video["id"], status="running", current_phase="download")

    result = run_to_phase(db, settings, video["id"], target_phase="download")
    assert result["status"] == "busy"


def test_run_to_phase_invalid_start_phase(db, settings):
    video = db.create_video(
        "https://example.com/q1.mp4", content_type="question", external_id="Q001"
    )

    result = run_to_phase(
        db, settings, video["id"], target_phase="assemble", start_phase="interaction_generate"
    )
    assert result["status"] == "invalid_phase"
    assert "不适用于该视频类型" in result["message"]


def test_run_to_phase_start_after_target(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )

    result = run_to_phase(
        db, settings, video["id"], target_phase="download", start_phase="transcribe"
    )
    assert result["status"] == "invalid_phase"
    assert "起始阶段晚于目标阶段" in result["message"]


def test_run_to_phase_completed_video_returns_run_to(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    db.update_video(video["id"], status="completed", current_phase="assemble")

    result = run_to_phase(db, settings, video["id"], target_phase="assemble")
    assert result["status"] == "run_to"


def test_run_to_phase_missing_url_not_waiting(db, settings):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    db.update_video(
        video["id"], status="missing_url", current_phase="download", error_message="lost url"
    )

    result = run_to_phase(db, settings, video["id"], target_phase="download")
    assert result["status"] in {"failed", "skipped"}
    assert "lost url" in result["message"]


def test_prepare_rerun_downgrades_transcribe_to_download_when_mp4_missing(db, settings):
    from server.app.services.manual_run import _prepare_rerun

    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    video_dir = settings.videos_dir / video["id"]
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text("old", encoding="utf-8")
    db.update_video(
        video["id"], storage_dir=str(video_dir), status="completed", current_phase="assemble"
    )

    normalized_start, _ = _prepare_rerun(db, settings, video, "transcribe")
    assert normalized_start == "download"
    assert not (video_dir / "subtitles.srt").exists()


def test_run_to_phase_start_transcribe_downgrades_and_runs_with_mock(db, settings, monkeypatch):
    video = db.create_video(
        "https://example.com/k1.mp4", content_type="knowledge", external_id="K001"
    )
    video_dir = settings.videos_dir / video["id"]
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text("old", encoding="utf-8")
    db.update_video(
        video["id"], storage_dir=str(video_dir), status="completed", current_phase="assemble"
    )

    monkeypatch.setattr(
        "server.app.worker.download_video",
        lambda url, output_path: output_path.write_bytes(b"fake"),
    )

    result = run_to_phase(
        db,
        settings,
        video["id"],
        target_phase="transcribe",
        start_phase="transcribe",
        providers=[TestProvider()],
    )
    assert result["status"] == "rerun_to"
    assert db.get_video(video["id"])["current_phase"] == "subtitle_review"


def test_submit_run_to_phase_returns_accepted(db, settings, monkeypatch):
    from unittest.mock import MagicMock

    from server.app.services.manual_run import submit_run_to_phase

    db.create_video("https://example.com/v1.mp4", "V1")
    db.update_video("v1", status="queued", current_phase="download")

    mock_submit = MagicMock()
    monkeypatch.setattr("server.app.services.manual_run._background_executor.submit", mock_submit)

    result = submit_run_to_phase(db, settings, "v1", target_phase="assemble")
    assert result["status"] == "accepted"
    mock_submit.assert_called_once()


def test_submit_run_to_phase_returns_busy(db, settings, monkeypatch):
    from unittest.mock import MagicMock

    from server.app.services.manual_run import submit_run_to_phase

    db.create_video("https://example.com/v1.mp4", "V1")
    db.update_video("v1", status="running", current_phase="download")

    mock_submit = MagicMock()
    monkeypatch.setattr("server.app.services.manual_run._background_executor.submit", mock_submit)

    result = submit_run_to_phase(db, settings, "v1", target_phase="assemble")
    assert result["status"] == "busy"
    mock_submit.assert_not_called()


def test_rerun_from_failed_phase_uses_current_phase(db, settings):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.update_video("knowledge_K001", status="failed", current_phase="chapter_generate")

    result = rerun_video_record(db, settings, "knowledge_K001", "__failed__")

    assert result["status"] == "rerun"
    assert result["phase"] == "chapter_generate"
    assert db.get_video("knowledge_K001")["current_phase"] == "chapter_generate"
    assert db.get_video("knowledge_K001")["status"] == "queued"


def test_rerun_from_failed_phase_skips_non_failed_video(db, settings):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.update_video("knowledge_K001", status="completed", current_phase="assemble")

    result = rerun_video_record(db, settings, "knowledge_K001", "__failed__")

    assert result["status"] == "skipped"
    assert "completed" in result["message"]
    assert db.get_video("knowledge_K001")["current_phase"] == "assemble"
    assert db.get_video("knowledge_K001")["status"] == "completed"


def test_batch_rerun_from_failed_phase_per_video(db, settings):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.create_video("https://example.com/k2.mp4", content_type="knowledge", external_id="K002")
    db.update_video("knowledge_K001", status="failed", current_phase="chapter_generate")
    db.update_video("knowledge_K002", status="failed", current_phase="subtitle_review")

    results = batch_rerun_video_records(
        db, settings, ["knowledge_K001", "knowledge_K002"], "__failed__"
    )

    assert results[0] == {
        "video_id": "knowledge_K001",
        "status": "rerun",
        "phase": "chapter_generate",
        "message": "",
    }
    assert results[1] == {
        "video_id": "knowledge_K002",
        "status": "rerun",
        "phase": "subtitle_review",
        "message": "",
    }
    assert db.get_video("knowledge_K001")["current_phase"] == "chapter_generate"
    assert db.get_video("knowledge_K002")["current_phase"] == "subtitle_review"


def test_batch_delete_uses_thread_pool(db, settings, monkeypatch):
    """batch_delete should use ThreadPoolExecutor for parallel directory deletion."""
    from unittest.mock import MagicMock, patch

    from server.app.pipeline.common import resolve_video_dir
    from server.app.services.video_actions import batch_delete_video_records

    v1 = db.create_video("https://example.com/v1.mp4", "V1")
    v2 = db.create_video("https://example.com/v2.mp4", "V2")

    d1 = resolve_video_dir(v1, settings.videos_dir)
    d2 = resolve_video_dir(v2, settings.videos_dir)
    d1.mkdir(parents=True, exist_ok=True)
    d2.mkdir(parents=True, exist_ok=True)

    mock_executor = MagicMock()
    mock_executor.__enter__ = MagicMock(return_value=mock_executor)
    mock_executor.__exit__ = MagicMock(return_value=False)
    mock_executor.map = MagicMock(return_value=[])

    with patch("server.app.services.video_actions.ThreadPoolExecutor", return_value=mock_executor):
        batch_delete_video_records(db, settings, [v1["id"], v2["id"]])

    mock_executor.map.assert_called_once()
