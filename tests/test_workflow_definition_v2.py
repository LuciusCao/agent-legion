from pathlib import Path

import pytest

from server.app.workflows.definition import (
    WorkflowDefinitionError,
    load_workflow_definition,
)


def test_loads_v2_edges_and_terminal(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
key: demo
label: Demo Workflow
schema_version: 2
nodes:
  parse:
    label: Parse
    capability: parse
    outputs: [parsed.json]
  classify:
    label: Classify
    capability: classify
    inputs: [parsed.json]
    outputs: [decision.json]
  uploadable:
    label: Uploadable
    capability: assemble
    terminal:
      outcome: uploadable
  non_uploadable:
    label: Non Uploadable
    capability: finalize
    terminal:
      outcome: non_uploadable
edges:
  - from: parse
    to: classify
  - from: classify
    to: uploadable
    when:
      artifact: decision.json
      path: $.eligible
      equals: true
  - from: classify
    to: non_uploadable
    when:
      artifact: decision.json
      path: $.eligible
      equals: false
""",
        encoding="utf-8",
    )

    definition = load_workflow_definition(path)

    assert definition.schema_version == 2
    assert definition.nodes["uploadable"].terminal is not None
    assert definition.nodes["uploadable"].terminal.outcome == "uploadable"
    assert [(edge.source, edge.target) for edge in definition.edges] == [
        ("parse", "classify"),
        ("classify", "uploadable"),
        ("classify", "non_uploadable"),
    ]
    assert definition.edges[1].condition is not None
    assert definition.edges[1].condition.artifact == "decision.json"
    assert definition.edges[1].condition.path == "$.eligible"
    assert definition.edges[1].condition.equals is True


def test_v1_after_is_converted_to_edges(tmp_path: Path) -> None:
    config = tmp_path / "v1_workflow.yaml"
    config.write_text(
        """
key: v1_test
label: V1 Test
schema_version: 1
nodes:
  fetch_questions:
    label: Fetch
    capability: fetch_questions
    after: []
  clean_and_parse:
    label: Clean
    capability: clean_and_parse
    after: [fetch_questions]
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(config)

    assert definition.schema_version == 1
    assert any(
        edge.source == "fetch_questions" and edge.target == "clean_and_parse"
        for edge in definition.edges
    )
    assert definition.nodes["clean_and_parse"].after == ["fetch_questions"]


def test_rejects_v2_with_unknown_edge_node(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
key: broken
label: Broken
schema_version: 2
nodes:
  a:
    label: A
    capability: a
edges:
  - from: a
    to: missing
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionError, match="Unknown edge target"):
        load_workflow_definition(path)


def test_loads_node_execution_overrides(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
key: agent_demo
label: Agent Demo
schema_version: 2
nodes:
  generate:
    capability: generate
    execution:
      provider: openai
      model: gpt-5
      thinking: high
      prompt: Check every citation.
edges: []
""",
        encoding="utf-8",
    )

    execution = load_workflow_definition(path).nodes["generate"].execution

    assert execution.provider == "openai"
    assert execution.model == "gpt-5"
    assert execution.thinking == "high"
    assert execution.prompt == "Check every citation."
