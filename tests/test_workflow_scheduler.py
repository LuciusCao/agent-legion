import json
from pathlib import Path

from server.app.workflows.conditions import condition_matches, selected_edges
from server.app.workflows.definition import (
    WorkflowCondition,
    WorkflowEdge,
    load_workflow_definition,
)
from server.app.workflows.scheduler import (
    find_ready_nodes,
    summarize_job_status,
)
from server.app.workflows.workflow_branching import (
    downstream_nodes,
    evaluate_branches,
)
from tests.helpers import load_builtin_definition


def _definition():
    return load_builtin_definition("education_video_problems_generation")


def test_find_ready_nodes_starts_with_root(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["intake_knowledge_points"]


def test_find_ready_nodes_requires_inputs(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["intake_knowledge_points"] = "completed"

    assert find_ready_nodes(definition, nodes, artifact_dir=tmp_path) == []

    (tmp_path / "knowledge_point.json").write_text("{}", encoding="utf-8")
    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["write_script", "generate_questions"]


def test_parallel_ready_nodes_after_intake(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["intake_knowledge_points"] = "completed"
    nodes["write_script"] = "completed"
    nodes["generate_questions"] = "completed"
    (tmp_path / "knowledge_point.json").write_text("{}", encoding="utf-8")
    (tmp_path / "script.md").write_text("x", encoding="utf-8")
    (tmp_path / "exercises.json").write_text("{}", encoding="utf-8")

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["review_script", "review_questions"]


def test_downstream_nodes_are_recursive():
    definition = _definition()

    downstream = downstream_nodes(definition, "write_script")

    assert "review_script" in downstream
    assert "publish_content" in downstream
    assert "generate_questions" not in downstream


def test_summarize_job_status():
    assert summarize_job_status([]) == "queued"
    assert summarize_job_status(["pending", "pending"]) == "queued"
    assert summarize_job_status(["completed", "running"]) == "running"
    assert summarize_job_status(["running", "failed"]) == "running"
    assert summarize_job_status(["completed", "failed"]) == "failed"
    assert summarize_job_status(["completed", "completed"]) == "completed"
    assert summarize_job_status(["completed", "stale"]) == "queued"


def test_condition_matches_artifact_json_path(tmp_path):
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False, "reason_code": "pure_calculation"}),
        encoding="utf-8",
    )

    condition = WorkflowCondition(
        artifact="decision.json",
        path="$.eligible",
        equals=False,
    )

    assert condition_matches(condition, tmp_path) is True


def test_condition_missing_artifact_is_not_match(tmp_path):
    condition = WorkflowCondition(
        artifact="decision.json",
        path="$.eligible",
        equals=True,
    )

    assert condition_matches(condition, tmp_path) is False


def test_selected_edges_filters_conditions(tmp_path):
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": True}),
        encoding="utf-8",
    )
    edges = [
        WorkflowEdge(source="classify", target="good", condition=None),
        WorkflowEdge(
            source="classify",
            target="uploadable",
            condition=WorkflowCondition("decision.json", "$.eligible", True),
        ),
        WorkflowEdge(
            source="classify",
            target="non_uploadable",
            condition=WorkflowCondition("decision.json", "$.eligible", False),
        ),
    ]

    assert [edge.target for edge in selected_edges(edges, tmp_path)] == [
        "good",
        "uploadable",
    ]


def test_summarize_job_status_treats_not_applicable_as_terminal():
    assert summarize_job_status(["completed", "not_applicable"]) == "completed"
    assert summarize_job_status(["completed", "not_applicable", "failed"]) == "failed"
    assert summarize_job_status(["pending", "not_applicable"]) == "queued"


def _write_branching_definition(path: Path) -> None:
    path.write_text(
        """
key: branching
label: Branching
schema_version: 2
nodes:
  root:
    label: Root
    capability: root
  gate:
    label: Gate
    capability: gate
    after: [root]
  good:
    label: Good
    capability: good
    after: [gate]
  leaf:
    label: Leaf
    capability: leaf
    after: [good]
  skipped:
    label: Skipped
    capability: skipped
edges:
  - {from: gate, to: good, when: {artifact: decision.json, path: "$.eligible", equals: true}}
  - {from: gate, to: skipped, when: {artifact: decision.json, path: "$.eligible", equals: false}}
  - {from: good, to: leaf}
""",
        encoding="utf-8",
    )


def test_evaluate_branches_marks_unselected_branch_not_applicable(tmp_path):
    path = tmp_path / "branching.yaml"
    _write_branching_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False}),
        encoding="utf-8",
    )
    statuses = {key: "pending" for key in definition.nodes}
    statuses["root"] = "completed"
    statuses["gate"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable
    assert "leaf" in result.not_applicable
    assert "skipped" not in result.not_applicable


def test_evaluate_branches_marks_node_not_applicable_when_all_incoming_conditions_false(tmp_path):
    path = tmp_path / "branching.yaml"
    _write_branching_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False}),
        encoding="utf-8",
    )
    statuses = {key: "pending" for key in definition.nodes}
    statuses["gate"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable


def test_unconditional_fanout_is_not_marked_not_applicable(tmp_path):
    path = tmp_path / "fanout.yaml"
    path.write_text(
        """
key: fanout
label: Fanout
nodes:
  root:
    label: Root
    capability: root
  left:
    label: Left
    capability: left
    after: [root]
  right:
    label: Right
    capability: right
    after: [root]
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    statuses = {key: "pending" for key in definition.nodes}
    statuses["root"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert result.not_applicable == set()
