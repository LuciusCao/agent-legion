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
        node_body="label: Fetch\ncapability: fetch_items\noutputs: [out.json]",
    )
    assert load_workflow_definition(path).nodes["one"].capability == "fetch_items"


def test_load_demo_workflow_definition():
    definition = load_builtin_definition("education_video_problems_generation")

    assert definition.key == "education_video_problems_generation"
    assert definition.label == "教学视频脚本与题目生成（示例）"
    # Legacy intake modes are retired (#154): the demo DAG declares none.
    assert definition.intake.modes == {}
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
    assert set(definition.intake.modes) == set()

    assert list(definition.nodes) == [
        "_start",
        "intake_knowledge_points",
        "write_script",
        "review_script",
        "generate_questions",
        "review_questions",
        "publish_content",
    ]
    start = definition.nodes["_start"]
    assert start.node_type == "start"
    assert start.accepted_item_types == ("material",)
    assert definition.start_node is start
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
            "capability: fetch_items\noutputs: [out.json]\n"
            "config:\n      page_size: 20\n      subject_id: math"
        ),
    )
    node = load_workflow_definition(path).nodes["one"]
    assert node.config == {"page_size": 20, "subject_id": "math"}


def test_workflow_node_config_defaults_to_empty(tmp_path: Path) -> None:
    path = write_workflow(tmp_path, node_body="capability: fetch_items\noutputs: [out.json]")
    assert load_workflow_definition(path).nodes["one"].config == {}


def test_workflow_node_rejects_non_mapping_config(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_items\noutputs: [out.json]\nconfig: [page_size]",
    )
    with pytest.raises(WorkflowDefinitionError, match="config must be a mapping"):
        load_workflow_definition(path)


def test_workflow_node_rejects_retired_resources_key(tmp_path: Path) -> None:
    # The resource provider/binding chain is retired; the loader fails fast on
    # the legacy node field so stale DAGs surface immediately.
    path = write_workflow(
        tmp_path,
        node_body="capability: fetch_items\noutputs: [out.json]\nresources: [question_detail]",
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
        node_body=("capability: fetch_items\noutputs: [out.json]\nconfig:\n      page_size: 20"),
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
        '"nodes":{"one":{"key":"one","label":"One","capability":"fetch_items",'
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


@pytest.mark.no_db
def test_node_config_schema_loads(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body=(
            "capability: fetch_items\n"
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    page_size:\n"
            "      type: integer\n"
            "      default: 50"
        ),
    )
    node = load_workflow_definition(path).nodes["one"]
    assert node.config_schema == {
        "type": "object",
        "properties": {"page_size": {"type": "integer", "default": 50}},
    }


@pytest.mark.no_db
def test_node_config_schema_defaults_to_empty(tmp_path: Path) -> None:
    path = write_workflow(tmp_path, node_body="capability: fetch_items")
    assert load_workflow_definition(path).nodes["one"].config_schema == {}


@pytest.mark.no_db
def test_node_config_schema_rejects_invalid_schema(tmp_path: Path) -> None:
    path = write_workflow(
        tmp_path,
        node_body=(
            "capability: fetch_items\n"
            "config_schema:\n"
            "  properties:\n"
            "    page_size:\n"
            "      type: array"
        ),
    )
    with pytest.raises(WorkflowDefinitionError, match="config_schema"):
        load_workflow_definition(path)


@pytest.mark.no_db
def test_node_config_schema_rejects_reserved_execution_keys(tmp_path: Path) -> None:
    """timeout_seconds/sandbox_network are platform-reserved (P-0.5): nodes
    set them via config/workspace overrides, never via config_schema."""
    for key in ("timeout_seconds", "sandbox_network"):
        path = write_workflow(
            tmp_path,
            node_body=(
                "capability: fetch_items\n"
                "config_schema:\n"
                "  properties:\n"
                f"    {key}:\n"
                "      type: integer"
            ),
        )
        with pytest.raises(WorkflowDefinitionError, match="reserved"):
            load_workflow_definition(path)


@pytest.mark.no_db
def test_node_config_schema_survives_revision_snapshot_round_trip(tmp_path: Path) -> None:
    """The declaration versions with the revision snapshot (asdict JSON) and
    with the Studio YAML export."""
    import json

    from server.app.services.workflow_revision_format import (
        definition_to_yaml,
        serialize_definition,
    )
    from server.app.workflows.definition import (
        workflow_definition_from_dict,
        workflow_definition_from_mapping,
    )

    schema = {"type": "object", "properties": {"page_size": {"type": "integer", "default": 50}}}
    path = write_workflow(
        tmp_path,
        node_body=(
            "capability: fetch_items\n"
            "config_schema:\n"
            "  type: object\n"
            "  properties:\n"
            "    page_size:\n"
            "      type: integer\n"
            "      default: 50"
        ),
    )
    definition = load_workflow_definition(path)

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    assert restored.nodes["one"].config_schema == schema

    import yaml

    reloaded = workflow_definition_from_mapping(yaml.safe_load(definition_to_yaml(definition)))
    assert reloaded.nodes["one"].config_schema == schema


def test_workflow_definition_from_dict_converts_corrupt_shapes_to_definition_error():
    """Codex P1 on PR #243: structurally corrupt persisted payloads (nodes as
    a list, null entries) used to escape as AttributeError/TypeError —
    killing the whole workflow-worker scan and startup instead of degrading
    just that workspace. All corrupt shapes must surface as the definition
    error the scan path handles per-workspace."""
    from server.app.workflows.definition import workflow_definition_from_dict
    from server.app.workflows.schema import WorkflowDefinitionError

    corrupt_payloads = [
        {"key": "k", "nodes": [{"a": 1}]},  # nodes is a list
        {"key": "k", "nodes": {"a": None}},  # null node entry
        {"key": "k", "nodes": {"a": "string"}},  # non-mapping node entry
        {"key": "k", "edges": {"a": 1}},  # edges is a mapping
        {"key": "k", "edges": [None]},  # null edge entry
        {"key": "k", "edges": ["string"]},  # non-mapping edge entry
        # subagent review on PR #243: nested terminal/condition corruption
        # used to escape as TypeError/ValueError/AttributeError — same
        # worker-killing path as the shapes above.
        {"key": "k", "nodes": {"a": {"terminal": "x"}}},  # terminal as str
        {"key": "k", "nodes": {"a": {"terminal": 5}}},  # terminal as int
        {"key": "k", "edges": [{"condition": "y"}]},  # condition as str
        {"key": "k", "edges": [{"when": ["y"]}]},  # when as list
    ]
    for payload in corrupt_payloads:
        with pytest.raises(WorkflowDefinitionError):
            workflow_definition_from_dict(payload)
