from pathlib import Path

import pytest

from server.app.pipelines.definition import PipelineDefinitionError, load_pipeline_definition


def test_load_question_content_definition():
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))

    assert definition.key == "question_content"
    assert definition.label == "题目内容生成"
    assert definition.concurrency.local == 8
    assert definition.concurrency.agent == 2
    assert definition.intake.modes["question_ids"].resolver == "direct.question_ids"
    assert definition.intake.modes["question_ids"].task_entity == "question"
    assert definition.intake.modes["knowledge_codes"].resolver == "cms.questions_by_knowledge"
    assert definition.intake.modes["knowledge_codes"].resource == "questions_by_knowledge"
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


def test_reject_unknown_dependency(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text(
        """
key: bad
label: Bad
nodes:
  second:
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
    runner: local
    after: [b]
  b:
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
    runner: remote
""",
        encoding="utf-8",
    )

    with pytest.raises(PipelineDefinitionError, match="runner"):
        load_pipeline_definition(config)
