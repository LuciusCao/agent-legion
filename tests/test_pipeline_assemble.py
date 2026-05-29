import json

import pytest

from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.upload_params import build_upload_params


def test_assemble_video_creates_metadata_and_report(tmp_path):
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:02,000 --> 00:00:05,000\nWorld\n",
        encoding="utf-8",
    )
    (video_dir / "chapters.json").write_text(
        json.dumps([{"id": "C1", "start_time": 0, "end_time": 5, "title": "Intro"}]),
        encoding="utf-8",
    )
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "interactions": [{"id": "I1", "trigger_time": 1, "type": "quiz", "question": "Q1"}],
            }
        ),
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
        "source_uuid": "source-uuid-1",
    }
    metadata = assemble_video(video, video_dir)

    assert metadata["video_id"] == "knowledge_K001"
    assert metadata["title"] == "Test Video"
    assert metadata["duration"] == 5.0
    assert metadata["content_type"] == "knowledge"
    assert metadata["source_uuid"] == "source-uuid-1"
    assert len(metadata["chapters"]) == 1
    assert len(metadata["interactions"]) == 1
    assert metadata["interactions"][0]["id"] == "I1"
    assert metadata["interactions"][0]["trigger_time"] == 1
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

    video = {
        "id": "question_Q001",
        "title": "Q1",
        "content_type": "question",
        "external_id": "Q001",
    }
    metadata = assemble_video(video, video_dir)

    assert metadata["interactions"] == []
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


def test_upload_params_uses_checklist_node_issues_for_interaction_review(tmp_path):
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {
                        "id": "node-1",
                        "type": "example_practice",
                        "trigger_time": 1,
                        "instruction": "试一试",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"score": 80, "status": "published"}), encoding="utf-8"
    )
    (video_dir / "checklist.json").write_text(
        json.dumps(
            {
                "checklist": {
                    "interaction_timing": {
                        "issues": [
                            {
                                "node_id": "node-1",
                                "title": "触发过早",
                                "details": "应在讲解结束后触发",
                            }
                        ]
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    params = build_upload_params({"id": "v1"}, video_dir)

    trial = params["example_problem_trial_json"][0]
    assert trial["review_status"] == 2
    assert trial["review_msg"] == "触发过早：应在讲解结束后触发"


def test_validate_phase_outputs_rejects_chapter_without_end_time(tmp_path):
    """validate_phase_outputs must raise when a chapter lacks end_time."""
    from server.app.pipeline.validators import validate_phase_outputs

    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "chapters.json").write_text(
        json.dumps(
            [
                {"start_time": 0, "end_time": 10, "title": "引入"},
                {"start_time": 18, "title": "讲解"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing 'end_time'"):
        validate_phase_outputs(video_dir, "chapter_generate")


def test_validate_phase_outputs_passes_with_complete_chapters(tmp_path):
    """validate_phase_outputs should not raise when all chapters have end_time."""
    from server.app.pipeline.validators import validate_phase_outputs

    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "chapters.json").write_text(
        json.dumps(
            [
                {"start_time": 0, "end_time": 10, "title": "引入"},
                {"start_time": 10, "end_time": 60, "title": "讲解"},
            ]
        ),
        encoding="utf-8",
    )

    validate_phase_outputs(video_dir, "chapter_generate")
