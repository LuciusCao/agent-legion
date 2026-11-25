"""Stable shared-resource declarations and run-pin projections (#1553 M43)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError

_RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _parse_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        loaded = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_shared_resources(
    raw_node: Mapping[str, Any], node_key: str, node_type: str
) -> tuple[str, ...]:
    value = raw_node.get("shared_resources")
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WorkflowDefinitionError(
            f"Node {node_key}.shared_resources must be a list of stable resource ids;"
            " version, manifest_hash, binding_revision and latest are not allowed"
        )
    resources: list[str] = []
    for resource_id in value:
        if not _RESOURCE_ID.fullmatch(resource_id) or resource_id.lower() == "latest":
            raise WorkflowDefinitionError(
                f"Node {node_key}.shared_resources contains an invalid stable resource id;"
                " select versions through the shared-resource library, never workflow YAML"
            )
        if resource_id in resources:
            raise WorkflowDefinitionError(
                f"Node {node_key}.shared_resources contains duplicate id {resource_id!r}"
            )
        resources.append(resource_id)
    if resources and node_type != "code":
        raise WorkflowDefinitionError(
            f"Node {node_key}.shared_resources is supported only on Host code nodes"
        )
    return tuple(resources)


def validate_definition_shared_resources(definition: Any) -> None:
    for node in definition.nodes.values():
        load_shared_resources(
            {"shared_resources": list(node.shared_resources)}, node.key, node.node_type
        )


def declarations_for(definition: Any) -> dict[str, list[str]]:
    return {
        node.key: list(node.shared_resources)
        for node in definition.executable_nodes.values()
        if node.shared_resources
    }


def make_resource_freeze(
    nodes: Mapping[str, list[str]], pins: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {str(pin["resource_id"]): pin for pin in pins}
    if set(by_id) != {resource_id for ids in nodes.values() for resource_id in ids}:
        raise ValueError("shared resource freeze does not cover the workflow declarations")
    bindings = {
        resource_id: {
            "version_id": str(by_id[resource_id]["version_id"]),
            "manifest_hash": str(by_id[resource_id]["manifest_hash"]),
            "binding_revision": int(by_id[resource_id]["binding_revision"]),
        }
        for resource_id in sorted(by_id)
    }
    digest = json.dumps(
        {"nodes": nodes, "bindings": bindings},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "pins_hash": hashlib.sha256(digest).hexdigest(),
        "nodes": {key: list(ids) for key, ids in nodes.items()},
        "pins": sorted(pins, key=lambda pin: str(pin["resource_id"])),
    }


def resource_freeze_from_run(run: Mapping[str, Any] | None) -> dict[str, Any]:
    if run is None:
        return {}
    pins = _parse_object(run.get("frozen_pins_json"))
    freeze = pins.get("shared_resources")
    return dict(freeze) if isinstance(freeze, Mapping) else {}


def resource_pins_hash(freeze: Mapping[str, Any]) -> str:
    return str(freeze.get("pins_hash") or "")


def resource_ids_for_node(freeze: Mapping[str, Any], node_key: str) -> set[str]:
    nodes = freeze.get("nodes")
    ids = nodes.get(node_key) if isinstance(nodes, Mapping) else None
    if not isinstance(ids, list):
        return set()
    return {str(resource_id) for resource_id in ids}


def public_resource_pins(pins: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """API-safe source/version/file view; internal selection and object ids stay out."""
    return [
        {
            key: value
            for key, value in {
                "resource_id": str(pin["resource_id"]),
                "name": str(pin.get("name") or ""),
                "version": int(pin["version"]),
                "manifest_hash": str(pin["manifest_hash"]),
                "source": pin["source"],
                "files": [
                    {
                        "path": str(file["path"]),
                        "sha256": str(file["sha256"]),
                        "size_bytes": int(file["size_bytes"]),
                        "content_type": str(file["content_type"]),
                    }
                    for file in pin["files"]
                ],
            }.items()
        }
        for pin in sorted(pins, key=lambda item: str(item["resource_id"]))
    ]


def public_resources_from_run(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    freeze = resource_freeze_from_run(run)
    pins = freeze.get("pins")
    if not isinstance(pins, list):
        return []
    return public_resource_pins([pin for pin in pins if isinstance(pin, Mapping)])


def without_internal_pins(run: Mapping[str, Any]) -> dict[str, Any]:
    pins = _parse_object(run.get("frozen_pins_json"))
    pins.pop("shared_resources", None)
    return pins
