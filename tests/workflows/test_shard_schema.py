from __future__ import annotations

import pytest

from server.app.workflows.loader import (
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.schema import WorkflowDefinitionError


def _wf(node_overrides: dict) -> dict:
    return {
        "key": "wf",
        "label": "wf",
        "nodes": {
            "parse": {"capability": "parse_questions", "outputs": ["questions.json"]},
            "review": {
                "capability": "review_keywords",
                "after": ["parse"],
                "inputs": ["questions.json"],
                **node_overrides.get("review", {}),
            },
            "aggregate": {
                "capability": "merge_reviews",
                "after": ["review"],
                **node_overrides.get("aggregate", {}),
            },
        },
    }


def test_shard_over_inputs_parses():
    wf = workflow_definition_from_mapping(
        _wf({"review": {"shard": {"over": "inputs.questions.json", "max_concurrency": 20}}})
    )
    node = wf.nodes["review"]
    assert node.shard is not None
    assert node.shard.over == "inputs.questions.json"
    assert node.shard.max_concurrency == 20
    assert node.shard.max_shards == 1000  # 默认硬上限


def test_shard_count_parses():
    wf = workflow_definition_from_mapping(_wf({"review": {"shard": {"count": 8}}}))
    assert wf.nodes["review"].shard.count == 8


def test_shard_over_must_reference_node_input():
    with pytest.raises(WorkflowDefinitionError, match="shard"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"over": "inputs.missing.json"}}})
        )


def test_shard_over_and_count_mutually_exclusive():
    with pytest.raises(WorkflowDefinitionError, match="shard"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"over": "inputs.questions.json", "count": 4}}})
        )


def test_shard_requires_over_or_count():
    with pytest.raises(WorkflowDefinitionError, match="shard"):
        workflow_definition_from_mapping(_wf({"review": {"shard": {}}}))


def test_reduce_must_reference_shard_node():
    with pytest.raises(WorkflowDefinitionError, match="reduce"):
        workflow_definition_from_mapping(_wf({"aggregate": {"reduce": {"from": "parse"}}}))


def test_reduce_parses_and_shard_reduce_not_same_node():
    wf = workflow_definition_from_mapping(
        _wf(
            {
                "review": {"shard": {"count": 4}},
                "aggregate": {"reduce": {"from": "review"}},
            }
        )
    )
    assert wf.nodes["aggregate"].reduce.from_node == "review"
    with pytest.raises(WorkflowDefinitionError, match="shard"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"count": 4}, "reduce": {"from": "review"}}})
        )


def test_max_shards_must_be_positive():
    with pytest.raises(WorkflowDefinitionError, match="max_shards"):
        workflow_definition_from_mapping(_wf({"review": {"shard": {"count": 4, "max_shards": 0}}}))


def test_shard_count_must_be_positive():
    with pytest.raises(WorkflowDefinitionError, match="shard.count"):
        workflow_definition_from_mapping(_wf({"review": {"shard": {"count": 0}}}))


def test_shard_max_concurrency_must_be_positive():
    with pytest.raises(WorkflowDefinitionError, match="shard.max_concurrency"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"count": 4, "max_concurrency": 0}}})
        )


def test_shard_over_must_use_inputs_prefix():
    with pytest.raises(WorkflowDefinitionError, match="shard.over"):
        workflow_definition_from_mapping(_wf({"review": {"shard": {"over": "questions.json"}}}))


def test_shard_must_be_a_mapping():
    with pytest.raises(WorkflowDefinitionError, match="shard must be a mapping"):
        workflow_definition_from_mapping(_wf({"review": {"shard": "yes"}}))


def test_reduce_from_is_required():
    with pytest.raises(WorkflowDefinitionError, match="reduce.from"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"count": 4}}, "aggregate": {"reduce": {}}})
        )
    with pytest.raises(WorkflowDefinitionError, match="reduce.from"):
        workflow_definition_from_mapping(
            _wf({"review": {"shard": {"count": 4}}, "aggregate": {"reduce": {"from": ""}}})
        )


def test_snapshot_round_trip_preserves_shard():
    # workflow_definition_from_dict 是 job snapshot 的还原路径：
    # shard/reduce 声明必须幸存，否则存量 running job resume 后丢分片语义
    raw = _wf({"review": {"shard": {"count": 4}}, "aggregate": {"reduce": {"from": "review"}}})
    wf = workflow_definition_from_mapping(raw)
    snapshot = {
        "key": wf.key,
        "label": wf.label,
        "schema_version": wf.schema_version,
        "intake": {},
        "nodes": raw["nodes"],
        "edges": [{"from": e.source, "to": e.target} for e in wf.edges],
    }
    restored = workflow_definition_from_dict(snapshot)
    assert restored.nodes["review"].shard.count == 4
    assert restored.nodes["aggregate"].reduce.from_node == "review"
