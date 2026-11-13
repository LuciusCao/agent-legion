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
            "nodes": {
                "fan_out": {
                    "type": "agent",
                    "label": "Fan Out",
                    "execution": {"runtime": "velites", "provider": "gateway", "model": "model-a"},
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


@pytest.mark.no_db
def test_capless_code_revision_round_trip_keeps_empty_string_field() -> None:
    definition = workflow_definition_from_mapping(
        {
            "key": "capless_round_trip",
            "label": "Capless Round Trip",
            "schema_version": 2,
            "nodes": {"collect": {"type": "code", "label": "Collect"}},
            "edges": [],
        }
    )

    published = serialize_definition(definition)
    restored = workflow_definition_from_dict(json.loads(published))
    assert serialize_definition(restored) == published
    payload = yaml.safe_load(definition_to_yaml(restored))
    assert payload["nodes"]["collect"]["capability"] == ""
    assert serialize_definition(workflow_definition_from_mapping(payload)) == published


def test_capless_code_publish_requires_node_key_code_and_echoes_empty_string(job_db) -> None:
    from server.app.services.node_codes import NodeCodeService
    from server.app.services.workflow_draft_publish import publish_workflow_draft

    workspace = job_db.create_workspace("Capless", workspace_id="capless_round_trip")
    workspace_id = str(workspace["id"])
    draft = "key: capless_round_trip\nlabel: Capless\nnodes:\n  collect:\n    type: code\n"

    ok, errors = publish_workflow_draft(job_db, workspace_id, draft)
    assert ok is False
    assert any("no published node code" in error for error in errors)

    codes = NodeCodeService(job_db.dsn_identity)
    codes.save_draft(
        workspace_id,
        "capless_round_trip",
        "collect",
        "def run(job, job_dir, runtime):\n    return {}\n",
        "test seed",
    )
    codes.publish(workspace_id, "capless_round_trip", "collect")
    ok, errors = publish_workflow_draft(job_db, workspace_id, draft)

    assert (ok, errors) == (True, [])
    active = job_db.get_active_workflow_revision(workspace_id, "capless_round_trip")
    assert active is not None
    definition = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    assert definition.nodes["collect"].capability == ""
    assert yaml.safe_load(definition_to_yaml(definition))["nodes"]["collect"]["capability"] == ""
    versions = codes.list_versions(workspace_id, "capless_round_trip", "collect")
    assert [row["status"] for row in versions] == ["published"]
