import json

import pytest

from server.app.pipeline.validators import (
    expected_outputs_exist,
    phase_outputs_sufficient,
    validate_phase_outputs,
)


def test_subtitle_review_missing_report(tmp_path):
    with pytest.raises(ValueError, match="Missing required file"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_missing_srt(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="subtitles_reviewed.srt is missing"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_invalid_report_type(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_valid(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "subtitles_reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
    )
    validate_phase_outputs(tmp_path, "subtitle_review")


def test_chapter_generate_missing_file(tmp_path):
    with pytest.raises(ValueError, match="Missing required file"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_not_a_list(tmp_path):
    (tmp_path / "chapters.json").write_text('"not_a_list"', encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a list of chapters"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_empty_list(tmp_path):
    (tmp_path / "chapters.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain at least one chapter"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_not_a_dict(tmp_path):
    (tmp_path / "chapters.json").write_text(json.dumps(["not_a_dict"]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_missing_end_time(tmp_path):
    (tmp_path / "chapters.json").write_text(json.dumps([{"title": "Ch1"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'end_time'"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_missing_title(tmp_path):
    (tmp_path / "chapters.json").write_text(json.dumps([{"end_time": 1}]), encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'title'"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_valid_with_end(tmp_path):
    (tmp_path / "chapters.json").write_text(
        json.dumps([{"title": "Ch1", "end": 1}]), encoding="utf-8"
    )
    validate_phase_outputs(tmp_path, "chapter_generate")


def test_interaction_generate_missing_file(tmp_path):
    with pytest.raises(ValueError, match="Missing required file"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_not_a_list(tmp_path):
    (tmp_path / "interactions.json").write_text('"not_a_list"', encoding="utf-8")
    with pytest.raises(ValueError, match="must contain an 'interactions' array"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_id(tmp_path):
    (tmp_path / "interactions.json").write_text(
        json.dumps([{"type": "example_practice", "trigger_time": 0, "instruction": "do it"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing 'id'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_not_a_dict(tmp_path):
    (tmp_path / "interactions.json").write_text(json.dumps(["not_a_dict"]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_unknown_type(tmp_path):
    (tmp_path / "interactions.json").write_text(
        json.dumps([{"id": "n1", "type": "unknown", "trigger_time": 0, "instruction": "do it"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown type 'unknown'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_trigger_time(tmp_path):
    (tmp_path / "interactions.json").write_text(
        json.dumps([{"id": "n1", "type": "example_practice", "instruction": "do it"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing 'trigger_time'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_instruction(tmp_path):
    (tmp_path / "interactions.json").write_text(
        json.dumps([{"id": "n1", "type": "example_practice", "trigger_time": 0}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing 'instruction'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_valid(tmp_path):
    (tmp_path / "interactions.json").write_text(
        json.dumps(
            [
                {"id": "n1", "type": "example_practice", "trigger_time": 0, "instruction": "do it"},
                {"id": "n2", "type": "video_summary", "trigger_time": 1, "instruction": "watch"},
                {
                    "id": "n3",
                    "type": "interaction_summary",
                    "trigger_time": 2,
                    "instruction": "pick",
                },
            ]
        ),
        encoding="utf-8",
    )
    validate_phase_outputs(tmp_path, "interaction_generate")


def test_content_review_missing_checklist(tmp_path):
    with pytest.raises(ValueError, match="Missing required file"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_invalid_checklist(tmp_path):
    (tmp_path / "checklist.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_missing_review(tmp_path):
    (tmp_path / "checklist.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing required file"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_invalid_review(tmp_path):
    (tmp_path / "checklist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "review_result.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_missing_reviews_array(tmp_path):
    (tmp_path / "checklist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "review_result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'reviews' array"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_valid(tmp_path):
    (tmp_path / "checklist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "review_result.json").write_text(json.dumps({"reviews": []}), encoding="utf-8")
    validate_phase_outputs(tmp_path, "content_review")


def test_expected_outputs_exist(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    assert expected_outputs_exist(tmp_path, ["a.txt", "b.txt"]) is False
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    assert expected_outputs_exist(tmp_path, ["a.txt", "b.txt"]) is True


def test_phase_outputs_sufficient_chapter_generate(tmp_path):
    assert phase_outputs_sufficient(tmp_path, "chapter_generate", []) is False
    (tmp_path / "chapters.json").write_text("[]", encoding="utf-8")
    assert phase_outputs_sufficient(tmp_path, "chapter_generate", []) is True


def test_phase_outputs_sufficient_subtitle_review(tmp_path):
    assert phase_outputs_sufficient(tmp_path, "subtitle_review", []) is False
    (tmp_path / "subtitles_reviewed.srt").write_text("x", encoding="utf-8")
    assert phase_outputs_sufficient(tmp_path, "subtitle_review", []) is True


def test_phase_outputs_sufficient_fallback(tmp_path):
    assert phase_outputs_sufficient(tmp_path, "download", ["file.txt"]) is False
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert phase_outputs_sufficient(tmp_path, "download", ["file.txt"]) is True
