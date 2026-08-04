import json

from server.app.pipeline.upload_params import build_upload_params, write_upload_params


def make_video(**overrides):
    defaults = {
        "id": "v1",
        "content_type": "knowledge",
        "title": "T",
        "duration": 10,
        "source_url": "https://example.com/v1.mp4",
        "external_id": "E001",
        "knowledge_code": "E001",
        "question_id": "",
    }
    defaults.update(overrides)
    return defaults


def test_upload_params_empty_interactions(tmp_path):
    video = make_video()
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    (video_dir / "chapters.json").write_text(
        json.dumps({"chapters": [{"id": "c1", "start_time": 0, "end_time": 1000, "title": "C1"}]})
    )
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}))
    (video_dir / "checklist.json").write_text(json.dumps({"checklist": {}}))
    (video_dir / "review_result.json").write_text(json.dumps({"reviews": []}))

    result = build_upload_params(video, video_dir)
    assert result["example_problem_trial_json"] == []
    assert result["interaction_summary_json"] == []


def test_upload_params_review_with_missing_item_id(tmp_path):
    video = make_video()
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}))
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {
                        "id": "n1",
                        "type": "example_practice",
                        "trigger_time": 0,
                        "instruction": "do",
                    }
                ]
            }
        )
    )
    (video_dir / "checklist.json").write_text(json.dumps({"checklist": {}}))
    (video_dir / "review_result.json").write_text(
        json.dumps({"reviews": [{"status": "published"}]})
    )

    result = build_upload_params(video, video_dir)
    # Review without item_id is skipped; interaction gets default pending_review (mapped to 2)
    trials = result["example_problem_trial_json"]
    assert len(trials) == 1
    assert trials[0]["review_status"] == 2


def test_upload_params_uses_subtitles_reviewed_when_present(tmp_path):
    video = make_video()
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\noriginal\n")
    (video_dir / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nreviewed\n"
    )
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}))
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}))
    (video_dir / "checklist.json").write_text(json.dumps({"checklist": {}}))
    (video_dir / "review_result.json").write_text(json.dumps({"reviews": []}))

    result = build_upload_params(video, video_dir)
    assert result["subtitles_json"][0]["text"] == "reviewed"


def test_upload_params_question_type_has_empty_trials(tmp_path):
    video = make_video(content_type="question", external_id="Q001", question_id="Q001")
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}))
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}))
    (video_dir / "checklist.json").write_text(json.dumps({"checklist": {}}))
    (video_dir / "review_result.json").write_text(json.dumps({"reviews": []}))

    result = build_upload_params(video, video_dir)
    assert result["example_problem_trial_json"] == []
    assert result["interaction_summary_json"] == []


def test_write_upload_params_creates_file(tmp_path):
    video = make_video()
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}))
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}))
    (video_dir / "checklist.json").write_text(json.dumps({"checklist": {}}))
    (video_dir / "review_result.json").write_text(json.dumps({"reviews": []}))

    path = write_upload_params(video, video_dir)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "subtitles_json" in data
    assert "clips_json" in data
