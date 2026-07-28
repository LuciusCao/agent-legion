from pathlib import Path

import pytest

from server.app.workflows.definition import WorkflowDefinitionError, load_workflow_definition


def write_workflow(tmp_path: Path, node_body: str) -> Path:
    config = tmp_path / "workflow.yaml"
    indented_body = node_body.replace("\n", "\n    ")
    config.write_text(
        f"""key: test
label: Test
nodes:
  one:
    {indented_body}
""",
        encoding="utf-8",
    )
    return config


def test_workflow_node_requires_non_empty_capability(tmp_path: Path) -> None:
    path = write_workflow(tmp_path, node_body="label: Fetch\noutputs: [out.json]")
    with pytest.raises(WorkflowDefinitionError, match="capability"):
        load_workflow_definition(path)


def test_workflow_node_loads_capability(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="label: Fetch\ncapability: fetch_questions\noutputs: [out.json]",
    )
    assert load_workflow_definition(path).nodes["one"].capability == "fetch_questions"


def test_load_question_comprehension_info_definition():
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))

    assert definition.key == "question_comprehension_info"
    assert definition.label == "题目审题信息生成 DAG"
    batch_by_ids = definition.intake.modes["batch_by_ids"]
    assert batch_by_ids.label == "按题目ID批量"
    assert batch_by_ids.input_field == "question_ids"
    batch_by_knowledge = definition.intake.modes["batch_by_knowledge"]
    assert batch_by_knowledge.label == "按知识点批量"
    assert batch_by_knowledge.input_field == "knowledge_codes"
    assert definition.nodes["fetch_questions"].label == "获取题目"
    assert definition.nodes["fetch_questions"].resources == [
        "question_detail",
        "by_knowledge",
    ]
    assert definition.nodes["clean_and_parse"].label == "清洗与解析"
    assert definition.nodes["clean_and_parse"].after == ["fetch_questions"]
    assert definition.nodes["assemble_comprehension_info"].inputs == [
        "questions_parsed_lean.json",
        "key_info_reviewed.json",
        "possible_errors_reviewed.json",
        "comprehension_difficulty.json",
    ]


def test_reject_unknown_dependency(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  second:
    capability: second
    after: [missing]
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="Unknown dependency"):
        load_workflow_definition(config)


def test_reject_cycle(tmp_path):
    config = tmp_path / "cycle.yaml"
    config.write_text(
        """
key: cycle
label: Cycle
nodes:
  a:
    capability: a
    after: [b]
  b:
    capability: b
    after: [a]
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="cycle"):
        load_workflow_definition(config)


def test_reject_workflow_concurrency(tmp_path):
    config = tmp_path / "bad-concurrency.yaml"
    config.write_text(
        """
key: bad
label: Bad
concurrency:
  local: 2
nodes:
  one:
    capability: one
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="Workflow field 'concurrency' was removed"):
        load_workflow_definition(config)


def test_reject_node_runner(tmp_path):
    config = tmp_path / "bad-runner.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: local
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="Node field 'runner' was removed"):
        load_workflow_definition(config)


def test_reject_node_agent(tmp_path):
    config = tmp_path / "bad-agent.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    agent:
      engine: pi
      skill: foo
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="Node field 'agent' was removed"):
        load_workflow_definition(config)


def test_node_label_fallback_to_key(tmp_path):
    config = tmp_path / "no-label.yaml"
    config.write_text(
        """
key: no_label
label: No Label
nodes:
  one:
    capability: one
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(config)
    assert definition.nodes["one"].label == "one"


def test_node_label_explicit(tmp_path):
    config = tmp_path / "with-label.yaml"
    config.write_text(
        """
key: with_label
label: With Label
nodes:
  one:
    label: 步骤一
    capability: one
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(config)
    assert definition.nodes["one"].label == "步骤一"


def test_load_question_comprehension_info_capabilities():
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))

    assert definition.key == "question_comprehension_info"
    assert definition.label == "题目审题信息生成 DAG"
    assert set(definition.intake.modes) == {"batch_by_knowledge", "batch_by_ids"}

    assert list(definition.nodes) == [
        "fetch_questions",
        "clean_and_parse",
        "classify_comprehension_eligibility",
        "generate_key_info",
        "review_key_info",
        "generate_possible_errors",
        "review_possible_errors",
        "assess_comprehension_difficulty",
        "assemble_comprehension_info",
        "finalize_non_uploadable",
    ]
    assert definition.nodes["fetch_questions"].capability == "fetch_questions"
    assert definition.nodes["clean_and_parse"].capability == "clean_and_parse"
    assert (
        definition.nodes["classify_comprehension_eligibility"].capability
        == "classify_comprehension_eligibility"
    )
    assert definition.nodes["generate_key_info"].after == ["clean_and_parse"]
    assert definition.nodes["review_key_info"].after == ["generate_key_info"]
    assert definition.nodes["generate_possible_errors"].after == ["review_key_info"]
    assert definition.nodes["review_possible_errors"].after == ["generate_possible_errors"]
    assert definition.nodes["assess_comprehension_difficulty"].after == [
        "review_key_info",
        "review_possible_errors",
    ]
    assert definition.nodes["assemble_comprehension_info"].after == [
        "assess_comprehension_difficulty"
    ]
    assert definition.nodes["assemble_comprehension_info"].outputs == [
        "comprehension_info.json",
        "manifest.json",
    ]
    assert definition.nodes["finalize_non_uploadable"].capability == "finalize_non_uploadable"
    assert definition.nodes["finalize_non_uploadable"].terminal is not None
    assert definition.nodes["finalize_non_uploadable"].terminal.outcome == "non_uploadable"


def test_workflow_node_loads_config_mapping(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body=(
            "capability: fetch_questions\noutputs: [out.json]\n"
            "config:\n      page_size: 20\n      subject_id: math"
        ),
    )
    node = load_workflow_definition(path).nodes["one"]
    assert node.config == {"page_size": 20, "subject_id": "math"}


def test_workflow_node_config_defaults_to_empty(tmp_path: Path) -> None:
    path = write_workflow(tmp_path, node_body="capability: fetch_questions\noutputs: [out.json]")
    assert load_workflow_definition(path).nodes["one"].config == {}


def test_workflow_node_rejects_non_mapping_config(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nconfig: [page_size]",
    )
    with pytest.raises(WorkflowDefinitionError, match="config must be a mapping"):
        load_workflow_definition(path)


def test_workflow_node_loads_resources(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nresources: [question_detail]",
    )
    definition = load_workflow_definition(path, resource_providers={"question_detail": {}})
    assert definition.nodes["one"].resources == ["question_detail"]


def test_workflow_node_resources_default_to_empty(tmp_path: Path) -> None:
    path = write_workflow(tmp_path, node_body="capability: fetch_questions\noutputs: [out.json]")
    assert load_workflow_definition(path).nodes["one"].resources == []


def test_workflow_node_rejects_non_list_resources(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nresources: question_detail",
    )
    with pytest.raises(WorkflowDefinitionError, match="resources must be a list of strings"):
        load_workflow_definition(path)


def test_workflow_node_rejects_unknown_resource(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nresources: [nope]",
    )
    with pytest.raises(WorkflowDefinitionError, match="unknown resource 'nope'"):
        load_workflow_definition(path, resource_providers={"question_detail": {}})


def test_node_resources_round_trip_through_job_snapshot(tmp_path: Path) -> None:
    from server.app.services.workflow_revision_format import (
        definition_from_job_snapshot,
        serialize_definition,
    )

    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nresources: [question_detail]",
    )
    definition = load_workflow_definition(path)
    snapshot = serialize_definition(definition)
    restored = definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot})
    assert restored is not None
    assert restored.nodes["one"].resources == ["question_detail"]


def test_node_config_round_trips_through_job_snapshot(tmp_path: Path) -> None:
    from server.app.services.workflow_revision_format import (
        definition_from_job_snapshot,
        serialize_definition,
    )

    path = write_workflow(
        tmp_path,
        node_body=(
            "capability: fetch_questions\noutputs: [out.json]\nconfig:\n      page_size: 20"
        ),
    )
    definition = load_workflow_definition(path)
    snapshot = serialize_definition(definition)
    restored = definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot})
    assert restored is not None
    assert restored.nodes["one"].config == {"page_size": 20}


def test_legacy_snapshot_without_node_config_still_loads() -> None:
    from server.app.services.workflow_revision_format import definition_from_job_snapshot

    snapshot = (
        '{"key":"test","label":"Test","schema_version":1,'
        '"nodes":{"one":{"key":"one","label":"One","capability":"fetch_questions",'
        '"after":[],"inputs":[],"outputs":["out.json"],"terminal":null,'
        '"execution":{"provider":"","model":"","thinking":"","prompt":""}}},"edges":[]}'
    )
    restored = definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot})
    assert restored is not None
    assert restored.nodes["one"].config == {}
