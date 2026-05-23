import json

from server.app.pipeline.assemble import assemble_video


def test_assemble_video_creates_metadata_and_report(tmp_path):
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\nWorld\n",
        encoding="utf-8",
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 5, "title": "Intro"}]),
        encoding="utf-8",
    )
    (video_dir / "interactions.json").write_text(
        json.dumps({
            "version": "1.0",
            "interactions": [
                {"id": "I1", "trigger_time": 1, "type": "quiz", "question": "Q1"}
            ],
        }),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"score": 95, "status": "published"}), encoding="utf-8"
    )

    video = {
        "id": "knowledge_K001",
        "title": "Test Video",
        "source_url": "https://example.com/k001.mp4",
        "content_type": "knowledge",
        "external_id": "K001",
        "knowledge_code": "K001",
        "question_id": "",
    }
    metadata = assemble_video(video, video_dir)

    assert metadata["video_id"] == "knowledge_K001"
    assert metadata["title"] == "Test Video"
    assert metadata["duration"] == 5.0
    assert metadata["content_type"] == "knowledge"
    assert len(metadata["chapters"]) == 1
    assert len(metadata["nodes"]) == 1
    assert metadata["nodes"][0]["id"] == "I1"
    assert metadata["nodes"][0]["trigger_time"] == 1
    assert metadata["review_details"]["score"] == 95
    assert metadata["status"] == "已完成"

    assert (video_dir / "metadata.json").exists()
    assert (video_dir / "report.md").exists()


def test_assemble_video_prefers_reviewed_subtitles(tmp_path):
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOriginal\n", encoding="utf-8"
    )
    (video_dir / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nReviewed\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )

    video = {"id": "g1", "title": "T"}
    metadata = assemble_video(video, video_dir)

    assert metadata["duration"] == 2.0
    assert metadata["subtitles"][0]["text"] == "Reviewed"


def test_assemble_video_creates_empty_interactions_stub_for_question(tmp_path):
    video_dir = tmp_path / "question_Q001"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    # interactions.json does NOT exist

    video = {"id": "question_Q001", "title": "Q1", "content_type": "question", "external_id": "Q001"}
    metadata = assemble_video(video, video_dir)

    assert metadata["nodes"] == []
    assert (video_dir / "interactions.json").exists()
    saved = json.loads((video_dir / "interactions.json").read_text(encoding="utf-8"))
    assert saved == {"version": "1.0", "interactions": []}


def test_assemble_video_without_review_result(tmp_path):
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps({"version": "1.0", "interactions": []}), encoding="utf-8"
    )

    video = {"id": "g1", "title": "T"}
    metadata = assemble_video(video, video_dir)

    assert metadata["review_details"] == {}
