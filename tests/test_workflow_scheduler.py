from pathlib import Path

from server.app.workflows.definition import load_workflow_definition
from server.app.workflows.scheduler import (
    downstream_nodes,
    find_ready_nodes,
    summarize_job_status,
)


def _definition():
    return load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))


def test_find_ready_nodes_starts_with_root(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["fetch_questions"]


def test_find_ready_nodes_requires_inputs(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["fetch_questions"] = "completed"

    assert find_ready_nodes(definition, nodes, artifact_dir=tmp_path) == []

    (tmp_path / "questions.json").write_text("{}", encoding="utf-8")
    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["clean_and_parse"]


def test_parallel_ready_nodes_after_understanding(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["fetch_questions"] = "completed"
    nodes["clean_and_parse"] = "completed"
    (tmp_path / "questions.json").write_text("{}", encoding="utf-8")
    (tmp_path / "questions_parsed.json").write_text("{}", encoding="utf-8")

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["generate_key_info"]


def test_downstream_nodes_are_recursive():
    definition = _definition()

    downstream = downstream_nodes(definition, "clean_and_parse")

    assert "generate_key_info" in downstream
    assert "review_possible_errors" in downstream
    assert "assemble_comprehension_info" in downstream


def test_summarize_job_status():
    assert summarize_job_status([]) == "queued"
    assert summarize_job_status(["pending", "pending"]) == "queued"
    assert summarize_job_status(["completed", "running"]) == "running"
    assert summarize_job_status(["running", "failed"]) == "running"
    assert summarize_job_status(["completed", "failed"]) == "failed"
    assert summarize_job_status(["completed", "completed"]) == "completed"
    assert summarize_job_status(["completed", "stale"]) == "queued"
