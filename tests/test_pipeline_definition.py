from pathlib import Path

import pytest

from server.app.pipelines.definition import PipelineDefinitionError, load_pipeline_definition


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
    path = write_pipeline(tmp_path, node_body="label: Fetch\nrunner: local\noutputs: [out.json]")
    with pytest.raises(PipelineDefinitionError, match="capability"):
        load_pipeline_definition(path)


def test_pipeline_node_loads_capability(tmp_path: Path) -> None:
    path = write_pipeline(
        tmp_path,
        node_body="label: Fetch\ncapability: fetch_questions\nrunner: local\noutputs: [out.json]",
    )
    assert load_pipeline_definition(path).nodes["one"].capability == "fetch_questions"


def test_load_question_content_definition():
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))

    assert definition.key == "question_content"
    assert definition.label == "题目内容生成"
    assert definition.concurrency.local == 8
    assert definition.concurrency.agent == 2
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


def test_load_reading_analysis_agent_contracts():
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))

    assert definition.key == "reading_analysis"
    assert definition.label == "题目审题分析 Pipeline"
    assert definition.concurrency.local == 4
    assert definition.concurrency.agent == 2
    assert set(definition.intake.modes) == {"batch_by_knowledge", "batch_by_ids"}

    node = definition.nodes["extract_keywords"]
    assert node.runner == "agent"
    assert node.agent is not None
    assert node.agent.engine == "pi"
    assert node.agent.skill == "reading_analysis/extract_keywords"
    assert node.agent.tools == ["read", "write", "bash"]
    assert definition.nodes["fetch_questions"].agent is None
    assert definition.nodes["clean_and_parse"].agent is None
    assert definition.nodes["mark_question"].agent is None


def test_reject_unknown_dependency(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  second:
    capability: second
    runner: local
    after: [missing]
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="Unknown dependency"):
        load_pipeline_definition(config)


def test_reject_cycle(tmp_path):
    config = tmp_path / "cycle.yaml"
    config.write_text(
        """
key: cycle
label: Cycle
nodes:
  a:
    capability: a
    runner: local
    after: [b]
  b:
    capability: b
    runner: local
    after: [a]
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="cycle"):
        load_pipeline_definition(config)


def test_reject_invalid_runner(tmp_path):
    config = tmp_path / "bad-runner.yaml"
    config.write_text(
        """
key: bad_runner
label: Bad Runner
nodes:
  one:
    capability: one
    runner: remote
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="runner"):
        load_pipeline_definition(config)


def test_reject_agent_block_on_local_node(tmp_path):
    config = tmp_path / "bad-agent-local.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: local
    agent:
      engine: pi
      skill: foo
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="agent block"):
        load_pipeline_definition(config)


def test_reject_unsupported_agent_engine(tmp_path):
    config = tmp_path / "bad-engine.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: agent
    agent:
      engine: other
      skill: foo
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="engine"):
        load_pipeline_definition(config)


def test_reject_empty_agent_skill(tmp_path):
    config = tmp_path / "bad-skill.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: agent
    agent:
      engine: pi
      skill: ""
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="skill"):
        load_pipeline_definition(config)


def test_reject_absolute_agent_skill(tmp_path):
    config = tmp_path / "bad-abs-skill.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: agent
    agent:
      engine: pi
      skill: /foo
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="skill"):
        load_pipeline_definition(config)


def test_reject_parent_traversal_agent_skill(tmp_path):
    config = tmp_path / "bad-traverse-skill.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: agent
    agent:
      engine: pi
      skill: foo/../bar
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="skill"):
        load_pipeline_definition(config)


def test_reject_invalid_agent_tool(tmp_path):
    config = tmp_path / "bad-tool.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  one:
    capability: one
    runner: agent
    agent:
      engine: pi
      skill: foo
      tools: [read, unknown]
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="tool"):
        load_pipeline_definition(config)


def test_legacy_agent_node_without_agent_block_is_readable(tmp_path):
    config = tmp_path / "legacy.yaml"
    config.write_text(
        """
key: legacy
label: Legacy
nodes:
  one:
    capability: one
    runner: agent
""",
        encoding="utf-8",
    )

    definition = load_pipeline_definition(config)
    assert definition.nodes["one"].runner == "agent"
    assert definition.nodes["one"].agent is None


def test_node_label_fallback_to_key(tmp_path):
    config = tmp_path / "no-label.yaml"
    config.write_text(
        """
key: no_label
label: No Label
nodes:
  one:
    capability: one
    runner: local
""",
        encoding="utf-8",
    )
    definition = load_pipeline_definition(config)
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
    runner: local
""",
        encoding="utf-8",
    )
    definition = load_pipeline_definition(config)
    assert definition.nodes["one"].label == "步骤一"


def test_loads_node_concurrency(tmp_path):
    config = tmp_path / "node-concurrency.yaml"
    config.write_text(
        """
key: node_concurrency
label: Node Concurrency
concurrency:
  local: 4
  agent: 2
  nodes:
    fetch_questions: 10
    clean_and_parse: 2
    mark_question: 10
nodes:
  fetch_questions:
    capability: fetch_questions
    runner: local
  clean_and_parse:
    capability: clean_and_parse
    runner: local
    after: [fetch_questions]
  mark_question:
    capability: mark_question
    runner: local
    after: [clean_and_parse]
""",
        encoding="utf-8",
    )
    definition = load_pipeline_definition(config)
    assert definition.concurrency.nodes == {
        "fetch_questions": 10,
        "clean_and_parse": 2,
        "mark_question": 10,
    }


def test_missing_nodes_defaults_to_empty(tmp_path):
    config = tmp_path / "no-node-concurrency.yaml"
    config.write_text(
        """
key: no_node_concurrency
label: No Node Concurrency
concurrency:
  local: 4
  agent: 2
nodes:
  one:
    capability: one
    runner: local
""",
        encoding="utf-8",
    )
    definition = load_pipeline_definition(config)
    assert definition.concurrency.nodes == {}


def test_invalid_node_limit_rejected(tmp_path):
    config = tmp_path / "bad-node-limit.yaml"
    config.write_text(
        """
key: bad_node_limit
label: Bad Node Limit
concurrency:
  local: 4
  agent: 2
  nodes:
    one: invalid
nodes:
  one:
    capability: one
    runner: local
""",
        encoding="utf-8",
    )
    with pytest.raises(PipelineDefinitionError, match="concurrency.nodes"):
        load_pipeline_definition(config)
