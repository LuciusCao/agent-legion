import json

import pytest
import yaml

from server.app.workflows.definition import (
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.revision_format import definition_to_yaml, serialize_definition


@pytest.mark.no_db
def test_revision_snapshot_and_yaml_round_trip_preserves_tools_config_and_fanout() -> None:
    definition = workflow_definition_from_mapping(
        {
            "key": "round_trip",
            "label": "Round Trip",
            "schema_version": 2,
            "execution": {"runtime": "velites", "provider": "gateway", "model": "model-a"},
            "nodes": {
                "fan_out": {
                    "type": "agent",
                    "label": "Fan Out",
                    "capability": "fan_out",
                    "tools": ["read", "uuid"],
                    "config": {"page_size": 20, "nested": {"enabled": True}},
                    "config_schema": {
                        "type": "object",
                        "properties": {"page_size": {"type": "integer", "default": 20}},
                    },
                    "shard": {"count": 3, "max_concurrency": 2, "max_shards": 9},
                    "outputs": ["items.json"],
                },
                "collect": {
                    "type": "code",
                    "label": "Collect",
                    "capability": "collect",
                    "after": ["fan_out"],
                    "inputs": ["items.json"],
                    "reduce": {"from": "fan_out"},
                },
            },
            "edges": [{"from": "fan_out", "to": "collect"}],
        }
    )

    published = serialize_definition(definition)
    restored = workflow_definition_from_dict(json.loads(published))
    assert serialize_definition(restored) == published

    echo = definition_to_yaml(definition)
    payload = yaml.safe_load(echo)
    assert payload["nodes"]["fan_out"]["tools"] == ["read", "uuid"]
    assert payload["nodes"]["fan_out"]["config"] == {
        "page_size": 20,
        "nested": {"enabled": True},
    }
    assert payload["nodes"]["fan_out"]["shard"] == {
        "count": 3,
        "max_concurrency": 2,
        "max_shards": 9,
    }
    assert payload["nodes"]["collect"]["reduce"] == {"from": "fan_out"}

    reparsed = workflow_definition_from_mapping(payload)
    assert serialize_definition(reparsed) == published
    assert definition_to_yaml(reparsed) == echo
