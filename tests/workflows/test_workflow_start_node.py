"""Start node loader rules and legacy auto-injection (EXEC-WORKFLOW-START-001, D1/D3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)

pytestmark = pytest.mark.no_db


def _definition(nodes: dict[str, Any], edges: list[dict[str, str]] | None = None):
    raw: dict[str, Any] = {"key": "wf", "label": "Wf", "nodes": nodes}
    if edges is not None:
        raw["schema_version"] = 2
        raw["edges"] = edges
    return workflow_definition_from_mapping(raw)


def _start_node(**extra: Any) -> dict[str, Any]:
    return {"type": "start", **extra}


def test_explicit_start_node_loads() -> None:
    definition = _definition(
        {
            "_start": _start_node(accepted_item_types=["material"]),
            "intake": {"capability": "intake"},
        },
        [{"from": "_start", "to": "intake"}],
    )

    start = definition.start_node
    assert start is not None
    assert start.key == "_start"
    assert start.node_type == "start"
    assert start.capability == ""
    assert start.accepted_item_types == ("material",)
    assert list(definition.executable_nodes) == ["intake"]


def test_start_defaults_accept_every_item_type() -> None:
    definition = _definition(
        {"_start": _start_node(), "intake": {"capability": "intake", "after": ["_start"]}}
    )
    assert definition.start_node is not None
    assert definition.start_node.accepted_item_types == ("material", "ref")


def test_two_start_nodes_are_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="exactly one start node"):
        _definition(
            {
                "_start": _start_node(),
                "other": _start_node(),
                "intake": {"capability": "intake", "after": ["_start"]},
            }
        )


def test_start_with_incoming_edge_is_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="must not have incoming edges"):
        _definition(
            {
                "_start": _start_node(),
                "intake": {"capability": "intake", "after": ["_start"]},
                "loop": {"capability": "loop", "after": ["intake"]},
            },
            [
                {"from": "_start", "to": "intake"},
                {"from": "intake", "to": "loop"},
                {"from": "loop", "to": "_start"},
            ],
        )


def test_start_without_outgoing_edge_is_rejected() -> None:
    # Isolated start + independent business node: no edge out of the start.
    with pytest.raises(WorkflowDefinitionError, match="at least one outgoing edge"):
        _definition(
            {
                "_start": _start_node(),
                "intake": {"capability": "intake"},
            }
        )


def test_start_with_conditional_outgoing_edge_is_rejected() -> None:
    # Start never executes, so a ``when`` artifact can never exist; the
    # conditional edge would silently mark the whole branch not_applicable.
    with pytest.raises(WorkflowDefinitionError, match="conditional outgoing edges"):
        _definition(
            {
                "_start": _start_node(),
                "intake": {"capability": "intake"},
            },
            [
                {
                    "from": "_start",
                    "to": "intake",
                    "when": {"artifact": "a.json", "path": "$.ok", "equals": True},
                }
            ],
        )


@pytest.mark.parametrize(
    "field",
    ["capability", "execution", "shard", "reduce", "terminal", "config", "config_schema"],
)
def test_start_must_not_declare_execution_fields(field: str) -> None:
    value: Any = (
        {"outcome": "done"} if field == "terminal" else ({} if field != "capability" else "x")
    )
    with pytest.raises(WorkflowDefinitionError, match=f"must not declare {field}"):
        _definition(
            {
                "_start": _start_node(**{field: value}),
                "intake": {"capability": "intake", "after": ["_start"]},
            }
        )


def test_start_rejects_empty_accepted_item_types() -> None:
    with pytest.raises(WorkflowDefinitionError, match="accepted_item_types"):
        _definition(
            {
                "_start": _start_node(accepted_item_types=[]),
                "intake": {"capability": "intake", "after": ["_start"]},
            }
        )


def test_start_rejects_unknown_item_type() -> None:
    with pytest.raises(WorkflowDefinitionError, match="accepted_item_types"):
        _definition(
            {
                "_start": _start_node(accepted_item_types=["material", "folder"]),
                "intake": {"capability": "intake", "after": ["_start"]},
            }
        )


def test_non_start_node_must_not_declare_accepted_item_types() -> None:
    with pytest.raises(WorkflowDefinitionError, match="only valid on a start node"):
        _definition({"intake": {"capability": "intake", "accepted_item_types": ["material"]}})


def test_unknown_node_type_is_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="type must be"):
        _definition({"intake": {"capability": "intake", "type": "trigger"}})


def test_missing_start_is_injected_with_edges_to_implicit_roots() -> None:
    definition = _definition(
        {
            "root_a": {"capability": "root_a"},
            "root_b": {"capability": "root_b"},
            "child": {"capability": "child", "after": ["root_a"]},
        }
    )

    start = definition.start_node
    assert start is not None
    assert start.key == "_start"
    assert start.accepted_item_types == ("material", "ref")
    assert {(edge.source, edge.target) for edge in definition.edges} == {
        ("_start", "root_a"),
        ("_start", "root_b"),
        ("root_a", "child"),
    }


def test_injected_start_avoids_key_collision() -> None:
    definition = _definition({"_start": {"capability": "legacy"}})
    start = definition.start_node
    assert start is not None
    assert start.key == "_start_1"
    assert definition.nodes["_start"].capability == "legacy"


def test_start_survives_snapshot_round_trip() -> None:
    from server.app.services.workflow_revision_format import serialize_definition

    definition = _definition(
        {
            "_start": _start_node(accepted_item_types=["ref"]),
            "intake": {"capability": "intake"},
        },
        [{"from": "_start", "to": "intake"}],
    )

    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    assert restored == definition


def test_injected_start_survives_snapshot_round_trip_symmetrically() -> None:
    """D3 hash symmetry: parse→serialize is identical for old (no start) and
    new (injected) parse results, so structural comparisons stay stable."""
    from server.app.services.workflow_revision_format import serialize_definition

    raw = {
        "key": "wf",
        "label": "Wf",
        "nodes": {
            "root": {"capability": "root"},
            "child": {"capability": "child", "after": ["root"]},
        },
    }
    first = workflow_definition_from_mapping(raw)
    restored = workflow_definition_from_dict(json.loads(serialize_definition(first)))
    assert restored == first
    assert serialize_definition(restored) == serialize_definition(first)


def test_injected_start_round_trip_with_multiple_derived_edges_keeps_edge_set() -> None:
    """≥2 after-derived edges + non-alphabetical writing order: the first
    round trip preserves the edge SET and all node data, but NOT the edge
    ORDER — serialize_definition sorts node keys, so the reload derives
    after-edges in alphabetical node order instead of the original writing
    order. This is pre-existing v1 behavior: readiness semantics depend on
    the edge set (scheduler readiness + acyclicity), not its order, and the
    snapshot is a fixed point from the second parse onward."""
    from server.app.services.workflow_revision_format import serialize_definition

    raw = {
        "key": "wf",
        "label": "Wf",
        "nodes": {
            # Deliberately non-alphabetical writing order.
            "zeta": {"capability": "zeta"},
            "alpha": {"capability": "alpha"},
            "mid": {"capability": "mid", "after": ["zeta"]},
            "leaf": {"capability": "leaf", "after": ["mid", "alpha"]},
        },
    }
    first = workflow_definition_from_mapping(raw)
    restored = workflow_definition_from_dict(json.loads(serialize_definition(first)))

    # Same nodes and same edge set; only the derived-edge order may differ.
    assert restored.nodes == first.nodes
    assert {(edge.source, edge.target) for edge in restored.edges} == {
        (edge.source, edge.target) for edge in first.edges
    }

    # From the second parse on the snapshot is a fixed point.
    again = workflow_definition_from_dict(json.loads(serialize_definition(restored)))
    assert again == restored
    assert serialize_definition(again) == serialize_definition(restored)


def test_start_survives_yaml_export_round_trip() -> None:
    from server.app.services.workflow_revision_format import definition_to_yaml

    definition = _definition(
        {
            "_start": _start_node(accepted_item_types=["material"]),
            "intake": {"capability": "intake"},
        },
        [{"from": "_start", "to": "intake"}],
    )

    reloaded = workflow_definition_from_mapping(yaml.safe_load(definition_to_yaml(definition)))
    assert reloaded == definition
    assert "type: start" in definition_to_yaml(definition)
    assert "capability" not in definition_to_yaml(definition).split("intake:")[0]
