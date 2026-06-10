from pathlib import Path

import pytest

from server.app.pipelines.artifacts import clear_rerun_outputs
from server.app.pipelines.definition import load_pipeline_definition


def test_clear_rerun_outputs_deletes_selected_and_downstream_only(tmp_path):
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    for node in definition.nodes.values():
        for output in node.outputs:
            (tmp_path / output).write_text("old", encoding="utf-8")

    cleared = clear_rerun_outputs(definition, "review_keywords", tmp_path)

    assert "keywords_reviewed.json" in cleared
    assert "difficulty_reviewed.json" in cleared
    assert "distractors_reviewed.json" in cleared
    assert "question_marks.json" in cleared
    assert (tmp_path / "keywords_raw.json").exists()
    assert not (tmp_path / "keywords_reviewed.json").exists()


def test_clear_rerun_outputs_leaves_runs_history_untouched(tmp_path):
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    for output in definition.nodes["extract_keywords"].outputs:
        (tmp_path / output).write_text("old", encoding="utf-8")
    runs_dir = tmp_path / "runs" / "extract_keywords" / "abc"
    runs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text("{}", encoding="utf-8")

    cleared = clear_rerun_outputs(definition, "extract_keywords", tmp_path)

    assert "keywords_raw.json" in cleared
    assert runs_dir.exists()
    assert (runs_dir / "run.json").exists()


def test_clear_rerun_outputs_rejects_escaping_paths(tmp_path):
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))

    with pytest.raises(ValueError, match="Unknown node"):
        clear_rerun_outputs(definition, "nonexistent", tmp_path)


def test_clear_rerun_outputs_returns_sorted_names(tmp_path):
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    for node in definition.nodes.values():
        for output in node.outputs:
            (tmp_path / output).write_text("old", encoding="utf-8")

    cleared = clear_rerun_outputs(definition, "fetch_questions", tmp_path)

    assert cleared == sorted(cleared)
    assert "questions.json" in cleared


def test_clear_rerun_outputs_skips_missing_files(tmp_path):
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    # Only create one output
    (tmp_path / "questions.json").write_text("old", encoding="utf-8")

    cleared = clear_rerun_outputs(definition, "fetch_questions", tmp_path)

    assert cleared == ["questions.json"]
