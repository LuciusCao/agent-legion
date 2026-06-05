from pathlib import Path

from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.scheduler import (
    downstream_nodes,
    find_ready_nodes,
    summarize_job_status,
)


def _definition():
    return load_pipeline_definition(Path("config/pipelines/question_content.yaml"))


def test_find_ready_nodes_starts_with_root(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["fetch_question_context"]


def test_find_ready_nodes_requires_inputs(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["fetch_question_context"] = "completed"

    assert find_ready_nodes(definition, nodes, artifact_dir=tmp_path) == []

    (tmp_path / "question_context.json").write_text("{}", encoding="utf-8")
    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["question_understanding"]


def test_parallel_ready_nodes_after_understanding(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["fetch_question_context"] = "completed"
    nodes["question_understanding"] = "completed"
    (tmp_path / "question_context.json").write_text("{}", encoding="utf-8")
    (tmp_path / "understanding.json").write_text("{}", encoding="utf-8")

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == [
        "misconception_analysis",
        "natural_language_reading",
        "solution_decomposition",
    ]


def test_downstream_nodes_are_recursive():
    definition = _definition()

    downstream = downstream_nodes(definition, "question_understanding")

    assert "misconception_analysis" in downstream
    assert "interactive_template_generation" in downstream
    assert "assemble_package" in downstream


def test_summarize_job_status():
    assert summarize_job_status([]) == "queued"
    assert summarize_job_status(["pending", "pending"]) == "queued"
    assert summarize_job_status(["completed", "running"]) == "running"
    assert summarize_job_status(["running", "failed"]) == "running"
    assert summarize_job_status(["completed", "failed"]) == "failed"
    assert summarize_job_status(["completed", "completed"]) == "completed"
    assert summarize_job_status(["completed", "stale"]) == "queued"
