from __future__ import annotations

import pytest

from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)


def _workflow(max_concurrency: object = 20) -> dict:
    return {
        "key": "test",
        "label": "Test",
        "nodes": {
            "agent": {
                "capability": "generate",
                "max_concurrency": max_concurrency,
            }
        },
    }


def test_node_max_concurrency_round_trips_through_snapshot() -> None:
    definition = workflow_definition_from_mapping(_workflow())
    assert definition.nodes["agent"].max_concurrency == 20

    restored = workflow_definition_from_dict(
        {
            "key": definition.key,
            "label": definition.label,
            "nodes": {
                "agent": {
                    "key": "agent",
                    "label": "agent",
                    "capability": "generate",
                    "max_concurrency": 20,
                }
            },
        }
    )
    assert restored.nodes["agent"].max_concurrency == 20


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_node_max_concurrency_must_be_positive_integer(value: object) -> None:
    with pytest.raises(WorkflowDefinitionError, match="max_concurrency"):
        workflow_definition_from_mapping(_workflow(value))


def test_node_max_concurrency_is_optional_for_local_compatibility() -> None:
    raw = _workflow()
    raw["nodes"]["agent"].pop("max_concurrency")
    definition = workflow_definition_from_mapping(raw)
    assert definition.nodes["agent"].max_concurrency is None
