import pytest

from server.app.workflows.builtin import (
    BUILTIN_WORKFLOW_DEFINITIONS,
    list_builtin_workflows,
    load_builtin_workflow,
)

pytestmark = pytest.mark.no_db


def test_builtin_workflow_keys() -> None:
    assert sorted(BUILTIN_WORKFLOW_DEFINITIONS) == [
        "question_comprehension_info",
        "video_knowledge",
    ]


def test_load_builtin_workflow_validates_and_matches_key() -> None:
    for key in BUILTIN_WORKFLOW_DEFINITIONS:
        definition = load_builtin_workflow(key)
        assert definition.key == key
        assert definition.nodes
        assert all(node.capability for node in definition.nodes.values())


def test_load_builtin_workflow_unknown_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        load_builtin_workflow("missing")


def test_list_builtin_workflows_covers_every_key() -> None:
    definitions = list_builtin_workflows()
    assert {definition.key for definition in definitions} == set(BUILTIN_WORKFLOW_DEFINITIONS)


def test_builtin_definitions_do_not_embed_executor_fields() -> None:
    for key, raw in BUILTIN_WORKFLOW_DEFINITIONS.items():
        assert "concurrency" not in raw, key
        for node_key, node in raw["nodes"].items():
            assert "runner" not in node, f"{key}.{node_key}"
            assert "agent" not in node, f"{key}.{node_key}"
