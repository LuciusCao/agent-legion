"""Snapshot declaration comparison for workflow upgrade guards."""

from __future__ import annotations

import json
from collections.abc import Mapping


def declarations_from_snapshot(snapshot: str) -> dict[str, frozenset[str]]:
    if not snapshot:
        return {}
    payload = json.loads(snapshot)
    if not isinstance(payload, Mapping):
        raise ValueError("workflow snapshot must be a mapping")
    nodes = payload.get("nodes")
    if nodes is None:
        return {}
    if not isinstance(nodes, Mapping):
        raise ValueError("workflow snapshot nodes must be a mapping")
    declarations: dict[str, frozenset[str]] = {}
    for key, node in nodes.items():
        if not isinstance(node, Mapping):
            raise ValueError("workflow snapshot node must be a mapping")
        resource_ids = node.get("shared_resources") or []
        if not isinstance(resource_ids, list) or any(
            not isinstance(resource_id, str) for resource_id in resource_ids
        ):
            raise ValueError("workflow shared_resources must be a list of stable ids")
        if resource_ids:
            declarations[str(key)] = frozenset(resource_ids)
    return declarations
