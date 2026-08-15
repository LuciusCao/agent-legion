from pathlib import Path

import pytest

from server.app.workflows.definition import WorkflowDefinitionError, load_workflow_definition
from tests.helpers import load_builtin_definition

_SNAPSHOT_LOGGER = "server.app.services.workflow_revision_format"


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


def test_load_demo_workflow_definition():
    definition = load_builtin_definition("education_video_problems_generation")

    assert definition.key == "education_video_problems_generation"
    assert definition.label == "教学视频脚本与题目生成（示例）"
    direct_ids = definition.intake.modes["direct_ids"]
    assert direct_ids.label == "按知识点批量"
    assert direct_ids.input_field == "knowledge_point_ids"
    assert definition.nodes["intake_knowledge_points"].label == "读取知识点"
    assert definition.nodes["write_script"].label == "撰写教学视频脚本"
    assert definition.nodes["write_script"].after == ["intake_knowledge_points"]
    assert definition.nodes["publish_content"].inputs == [
        "knowledge_point.json",
        "script.md",
        "script_review.json",
        "exercises.json",
        "exercises_review.json",
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


def test_load_demo_workflow_capabilities():
    definition = load_builtin_definition("education_video_problems_generation")

    assert definition.key == "education_video_problems_generation"
    assert definition.label == "教学视频脚本与题目生成（示例）"
    assert set(definition.intake.modes) == {"direct_ids"}

    assert list(definition.nodes) == [
        "intake_knowledge_points",
        "write_script",
        "review_script",
        "generate_questions",
        "review_questions",
        "publish_content",
    ]
    assert definition.nodes["intake_knowledge_points"].capability == "intake_knowledge_points"
    assert definition.nodes["write_script"].capability == "write_script"
    assert definition.nodes["review_script"].after == ["write_script"]
    assert definition.nodes["generate_questions"].after == ["intake_knowledge_points"]
    assert definition.nodes["review_questions"].after == ["generate_questions"]
    assert definition.nodes["publish_content"].after == ["review_script", "review_questions"]
    assert definition.nodes["publish_content"].outputs == ["publish_payload.json"]
    assert definition.nodes["publish_content"].terminal is not None
    assert definition.nodes["publish_content"].terminal.outcome == "published"


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


def test_workflow_node_rejects_retired_resources_key(tmp_path: Path) -> None:
    # The resource provider/binding chain is retired; the loader fails fast on
    # the legacy node field so stale DAGs surface immediately.
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_questions\noutputs: [out.json]\nresources: [question_detail]",
    )
    with pytest.raises(WorkflowDefinitionError, match="'resources' was removed"):
        load_workflow_definition(path)


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


@pytest.mark.no_db
def test_corrupt_job_snapshot_returns_none_and_logs_warning(caplog) -> None:
    import logging

    from server.app.services.workflow_revision_format import definition_from_job_snapshot

    job = {"id": "job-corrupt", "workflow_definition_snapshot_json": "{not valid json"}
    with caplog.at_level(logging.WARNING, logger=_SNAPSHOT_LOGGER):
        restored = definition_from_job_snapshot(job)

    assert restored is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "job-corrupt" in warnings[0].getMessage()


@pytest.mark.no_db
def test_missing_job_snapshot_returns_none_without_warning(caplog) -> None:
    import logging

    from server.app.services.workflow_revision_format import definition_from_job_snapshot

    with caplog.at_level(logging.WARNING, logger=_SNAPSHOT_LOGGER):
        assert definition_from_job_snapshot({"id": "job-legacy"}) is None
        assert (
            definition_from_job_snapshot(
                {"id": "job-legacy", "workflow_definition_snapshot_json": ""}
            )
            is None
        )

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
