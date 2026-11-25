from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from server.app.services.job_creation_pipeline import JobCreationPipeline
from server.app.services.job_errors import InvalidOperationError
from server.app.services.workflow_revision_format import (
    definition_hash,
    definition_to_yaml,
    serialize_definition,
    workflow_definition_to_response_payload,
)
from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.shared_resources import (
    make_resource_freeze,
    public_resource_pins,
)
from workspace_libs.node_sdk import NodeContext

pytestmark = pytest.mark.no_db


def _definition(resources, node_type: str = "code"):
    node = {"type": node_type, "capability": "draft", "shared_resources": resources}
    if node_type == "agent":
        node.update(
            {
                "execution": {"runtime": "velites", "provider": "p", "model": "m"},
                "skill": {"key": "group/skill"},
            }
        )
    return workflow_definition_from_mapping(
        {"key": "ws", "label": "Workspace", "nodes": {"draft": node}}
    )


def _pin(resource_id: str = "resource-1") -> dict:
    return {
        "resource_id": resource_id,
        "name": "Rules",
        "version_id": "version-row-1",
        "version": 2,
        "manifest_hash": "manifest-sha",
        "grant_id": "grant-secretish-id",
        "binding_revision": 4,
        "source": {"type": "material", "id": "m1", "workspace_id": "ws", "materials": []},
        "files": [
            {
                "path": "rules.txt",
                "sha256": "file-sha",
                "size_bytes": 4,
                "content_type": "text/plain",
                "storage_key": "private/object/key",
            }
        ],
    }


def _echo_reload(definition):
    return workflow_definition_from_mapping(yaml.safe_load(definition_to_yaml(definition)))


def test_empty_declaration_does_not_change_legacy_snapshot_shape_or_hash() -> None:
    definition = _definition([])
    baseline = workflow_definition_from_mapping(
        {
            "key": "ws",
            "label": "Workspace",
            "nodes": {"draft": {"type": "code", "capability": "draft"}},
        }
    )
    assert "shared_resources" not in json.loads(serialize_definition(definition))["nodes"]["draft"]
    assert definition_hash(serialize_definition(definition)) == definition_hash(
        serialize_definition(baseline)
    )
    assert definition_hash(serialize_definition(_echo_reload(definition))) == definition_hash(
        serialize_definition(_echo_reload(baseline))
    )


def test_stable_ids_round_trip_without_a_version() -> None:
    definition = _definition(["resource-1", "resource_2"])
    assert definition.nodes["draft"].shared_resources == ("resource-1", "resource_2")
    restored = workflow_definition_from_dict(json.loads(serialize_definition(definition)))
    assert restored.nodes["draft"].shared_resources == definition.nodes["draft"].shared_resources
    echoed = _echo_reload(definition)
    assert echoed.nodes["draft"].shared_resources == definition.nodes["draft"].shared_resources
    response_node = next(
        node
        for node in workflow_definition_to_response_payload(definition)["nodes"]
        if node["key"] == "draft"
    )
    assert response_node["shared_resources"] == ["resource-1", "resource_2"]
    assert "version" not in response_node


@pytest.mark.parametrize(
    "resources",
    [
        [{"resource_id": "resource-1"}],
        [{"resource_id": "resource-1", "version": "latest"}],
        ["resource-1@2"],
        ["resource-1:latest"],
        ["latest"],
        ["resource-1", "resource-1"],
    ],
)
def test_versioned_or_duplicate_declarations_fail_closed(resources) -> None:
    with pytest.raises(WorkflowDefinitionError, match="shared_resources"):
        _definition(resources)


def test_publish_validation_cannot_be_bypassed_with_a_dataclass() -> None:
    definition = _definition(["resource-1"])
    invalid_node = replace(definition.nodes["draft"], shared_resources=("latest",))
    invalid = replace(definition, nodes={**definition.nodes, "draft": invalid_node})
    with pytest.raises(WorkflowDefinitionError, match="shared_resources"):
        serialize_definition(invalid)


def test_only_host_code_nodes_declare_resources() -> None:
    with pytest.raises(WorkflowDefinitionError, match="only on Host code nodes"):
        _definition(["resource-1"], node_type="agent")


def test_legacy_job_batches_reject_resource_workflows_before_resolution() -> None:
    pipeline = JobCreationPipeline(object(), object())
    with pytest.raises(InvalidOperationError, match="require the /runs API"):
        pipeline.resolve_node_setup(_definition(["resource-1"]), {}, "ws", "ws")


def test_binding_revision_changes_only_internal_hash_and_public_view_is_safe() -> None:
    pin = _pin()
    first = make_resource_freeze({"draft": ["resource-1"]}, [pin])
    second = make_resource_freeze({"draft": ["resource-1"]}, [{**pin, "binding_revision": 5}])
    assert first["pins_hash"] != second["pins_hash"]
    public = public_resource_pins([pin])[0]
    assert public["version"] == 2 and public["files"][0]["sha256"] == "file-sha"
    assert "binding_revision" not in public and "grant_id" not in public
    assert "storage_key" not in public["files"][0]


def test_node_context_resource_view_is_recursively_read_only(tmp_path: Path) -> None:
    context = NodeContext(
        {},
        tmp_path,
        {"shared_resources": {"resource-1": {"version": 2, "files": [{"path": "a.txt"}]}}},
    )
    resources = context.shared_resources
    assert resources["resource-1"]["version"] == 2
    with pytest.raises(TypeError):
        resources["resource-1"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        resources["resource-1"]["version"] = 3  # type: ignore[index]
    with pytest.raises(AttributeError):
        resources["resource-1"]["files"].append({})  # type: ignore[attr-defined]
