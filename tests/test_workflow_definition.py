from pathlib import Path

import pytest

from server.app.workflows.definition import WorkflowDefinitionError, load_workflow_definition


def write_pipeline(tmp_path: Path, node_body: str) -> Path:
    config = tmp_path / "pipeline.yaml"
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


def test_pipeline_node_requires_non_empty_capability(tmp_path: Path) -> None:
    path = write_pipeline(tmp_path, node_body="label: Fetch\noutputs: [out.json]")
    with pytest.raises(WorkflowDefinitionError, match="capability"):
        load_workflow_definition(path)


def test_pipeline_node_loads_capability(tmp_path: Path) -> None:
    path = write_pipeline(
        tmp_path,
        node_body="label: Fetch\ncapability: fetch_questions\noutputs: [out.json]",
    )
    assert load_workflow_definition(path).nodes["one"].capability == "fetch_questions"


def test_load_question_content_definition():
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))

    assert definition.key == "question_content"
    assert definition.label == "题目内容生成"
    direct_mode = definition.intake.modes["direct_ids"]
    assert direct_mode.label == "直接输入 ID"
    assert direct_mode.input_field == "question_ids"
    assert direct_mode.resource == ""
    knowledge_mode = definition.intake.modes["by_knowledge"]
    assert knowledge_mode.label == "按知识点查询"
    assert knowledge_mode.input_field == "knowledge_codes"
    assert knowledge_mode.resource == "by_knowledge"
    assert definition.nodes["fetch_question_context"].label == "获取题目上下文"
    assert definition.nodes["question_understanding"].label == "题目理解"
    assert definition.nodes["question_understanding"].after == ["fetch_question_context"]
    assert definition.nodes["assemble_package"].inputs == [
        "question_context.json",
        "understanding.json",
        "natural_reading.md",
        "misconceptions.json",
        "solution_steps.json",
        "faq.json",
        "content_graph.json",
        "interactive_template.json",
        "review_result.json",
    ]


def test_load_reading_analysis_capabilities():
    definition = load_workflow_definition(Path("config/workflows/reading_analysis.yaml"))

    assert definition.key == "reading_analysis"
    assert definition.label == "题目审题分析 工作流"
    assert set(definition.intake.modes) == {"batch_by_knowledge", "batch_by_ids"}

    node = definition.nodes["extract_keywords"]
    assert node.capability == "extract_keywords"
    assert node.after == ["clean_and_parse"]
    assert definition.nodes["fetch_questions"].capability == "fetch_questions"
    assert definition.nodes["clean_and_parse"].capability == "clean_and_parse"
    assert definition.nodes["mark_question"].capability == "mark_question"


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


def test_reject_pipeline_concurrency(tmp_path):
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
        "generate_key_info",
        "review_key_info",
        "generate_possible_errors",
        "review_possible_errors",
        "assess_comprehension_difficulty",
        "assemble_comprehension_info",
    ]
    assert definition.nodes["fetch_questions"].capability == "fetch_questions"
    assert definition.nodes["clean_and_parse"].capability == "clean_and_parse"
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
