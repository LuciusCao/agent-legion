import pytest

from server.app.pipeline.validators import validate_phase_outputs

# --- subtitle_review ---


def test_subtitle_review_missing_report_fails(tmp_path):
    with pytest.raises(ValueError, match="Missing required file: subtitle_review_report.json"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_non_dict_report_fails(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text('"string"')
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_missing_srt_fails(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text("{}")
    with pytest.raises(ValueError, match="subtitles_reviewed.srt is missing"):
        validate_phase_outputs(tmp_path, "subtitle_review")


def test_subtitle_review_valid_passes(tmp_path):
    (tmp_path / "subtitle_review_report.json").write_text("{}")
    (tmp_path / "subtitles_reviewed.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    validate_phase_outputs(tmp_path, "subtitle_review")  # should not raise


# --- chapter_generate ---


def test_chapter_generate_missing_file_fails(tmp_path):
    with pytest.raises(ValueError, match="Missing required file: chapters.json"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_empty_list_fails(tmp_path):
    (tmp_path / "chapters.json").write_text('{"chapters": []}')
    with pytest.raises(ValueError, match="at least one chapter"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_missing_end_time_fails(tmp_path):
    (tmp_path / "chapters.json").write_text('[{"title": "T1", "start_time": 0}]')
    with pytest.raises(ValueError, match="missing 'end_time'"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_missing_title_fails(tmp_path):
    (tmp_path / "chapters.json").write_text('[{"start_time": 0, "end_time": 1}]')
    with pytest.raises(ValueError, match="missing 'title'"):
        validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_valid_passes(tmp_path):
    (tmp_path / "chapters.json").write_text('[{"title": "T1", "start_time": 0, "end_time": 1}]')
    validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_list_root_form_passes(tmp_path):
    """chapters.json may be a plain list at root."""
    (tmp_path / "chapters.json").write_text('[{"title": "T1", "start_time": 0, "end_time": 1}]')
    validate_phase_outputs(tmp_path, "chapter_generate")


def test_chapter_generate_non_dict_chapter_fails(tmp_path):
    (tmp_path / "chapters.json").write_text('["not a dict"]')
    with pytest.raises(ValueError, match="Chapter 1 must be an object"):
        validate_phase_outputs(tmp_path, "chapter_generate")


# --- interaction_generate ---


def test_interaction_generate_missing_file_fails(tmp_path):
    with pytest.raises(ValueError, match="Missing required file: interactions.json"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_id_fails(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"type": "example_practice", "trigger_time": 0, "instruction": "do it"}]}'
    )
    with pytest.raises(ValueError, match="missing 'id'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_unknown_type_fails(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "unknown", "trigger_time": 0, "instruction": "do it"}]}'
    )
    with pytest.raises(ValueError, match="unknown type 'unknown'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_trigger_time_fails(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "example_practice", "instruction": "do it"}]}'
    )
    with pytest.raises(ValueError, match="missing 'trigger_time'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_missing_instruction_fails(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "example_practice", "trigger_time": 0}]}'
    )
    with pytest.raises(ValueError, match="missing 'instruction'"):
        validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_valid_passes(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "example_practice", "trigger_time": 0, "instruction": "do it"}]}'
    )
    validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_video_summary_type_passes(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "video_summary", "trigger_time": 0, "instruction": "summarize"}]}'
    )
    validate_phase_outputs(tmp_path, "interaction_generate")


def test_interaction_generate_interaction_summary_type_passes(tmp_path):
    (tmp_path / "interactions.json").write_text(
        '{"interactions": [{"id": "i1", "type": "interaction_summary", "trigger_time": 0, "instruction": "review"}]}'
    )
    validate_phase_outputs(tmp_path, "interaction_generate")


# --- content_review ---


def test_content_review_missing_checklist_fails(tmp_path):
    with pytest.raises(ValueError, match="Missing required file: checklist.json"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_non_dict_checklist_fails(tmp_path):
    (tmp_path / "checklist.json").write_text("[]")
    (tmp_path / "review_result.json").write_text('{"reviews": []}')
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_missing_reviews_fails(tmp_path):
    (tmp_path / "checklist.json").write_text("{}")
    (tmp_path / "review_result.json").write_text("{}")
    with pytest.raises(ValueError, match="missing 'reviews' array"):
        validate_phase_outputs(tmp_path, "content_review")


def test_content_review_valid_passes(tmp_path):
    (tmp_path / "checklist.json").write_text('{"score": 90}')
    (tmp_path / "review_result.json").write_text('{"reviews": []}')
    validate_phase_outputs(tmp_path, "content_review")


def test_content_review_non_dict_review_fails(tmp_path):
    (tmp_path / "checklist.json").write_text("{}")
    (tmp_path / "review_result.json").write_text("[]")
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_phase_outputs(tmp_path, "content_review")
