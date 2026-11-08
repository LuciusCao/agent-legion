from __future__ import annotations

from typing import Any

from server.app.workflows.approval_node import strip_snapshot_placeholders
from server.app.workflows.snapshot_shape import ensure_mapping, snapshot_field


def snapshot_to_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    nodes_payload = snapshot_field(payload, "nodes", default={}, list_form=False)
    edges_payload = snapshot_field(payload, "edges", default=[], list_form=True)
    raw = {
        "key": payload.get("key"),
        "label": payload.get("label"),
        "schema_version": payload.get("schema_version", 1),
        "intake": payload.get("intake", {}),
        "execution": payload.get("execution"),
        "nodes": {},
        "edges": [],
    }
    for node_key, node in nodes_payload.items():
        raw_node = dict(node)
        # Snapshots store the dataclass field name; the yaml spelling is ``type``.
        if "node_type" in raw_node:
            raw_node["type"] = raw_node.pop("node_type")
        # #458: ``WorkflowReduceSpec.from_node`` serializes as ``from_node``
        # in asdict snapshots, but the yaml spelling the loader reads is
        # ``from`` (mirrors the node_type translation above). Existing
        # snapshots all carry ``from_node``; a ``from`` key passes through
        # unchanged (and wins if both ever appear).
        raw_reduce = raw_node.get("reduce")
        if isinstance(raw_reduce, dict) and "from_node" in raw_reduce:
            translated = dict(raw_reduce)
            translated.setdefault("from", translated.pop("from_node"))
            raw_node["reduce"] = translated
        # asdict snapshots carry every field on every node: strip the
        # per-type placeholders a start/approval node must not declare, and
        # the default contract copy on non-start nodes.
        strip_snapshot_placeholders(raw_node)
        ensure_mapping(raw_node.get("terminal"), f"node {node_key} 'terminal'")
        raw["nodes"][node_key] = raw_node
    for edge in edges_payload:
        raw_edge = {
            "from": edge.get("source") or edge.get("from"),
            "to": edge.get("target") or edge.get("to"),
        }
        condition = ensure_mapping(edge.get("condition") or edge.get("when"), "edge 'condition'")
        if condition:
            raw_edge["when"] = {
                "artifact": condition.get("artifact"),
                "path": condition.get("path"),
                "equals": condition.get("equals"),
            }
        raw["edges"].append(raw_edge)
    return raw
