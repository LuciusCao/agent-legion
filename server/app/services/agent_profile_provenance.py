"""Agent profile provenance: where an inlined node profile came from (#935).

The v93 backfill (``db/migrations/agent_profile_backfill``) inlines each
legacy agent node's published Agent definition into the node and records,
beside the stored revision definition (outside ``definition_hash``, same
rule as ``node_code_pins``)::

    agent_profile_provenance: {node_key: {agent_id, version,
                                          definition_hash, restore}}

``restore`` holds the node's five profile fields before inlining. Two
consumers:

* **workflow upgrade** (#440 P3 "升级 diff 归一"): an old job froze the
  legacy node; the active revision now carries the inlined one. Without
  normalization every upgrade would re-run every agent node. The node counts
  as unchanged when the old node equals the new node's *legacy view*
  (profile fields put back from ``restore``) — the definition diff — and the
  old run's identity hash equals the provenance ``definition_hash`` — the
  implementation identity. Both checks stay independent and conservative.
* **publish / runtime-only save**: provenance survives a later revision for
  every node whose profile fields are unchanged (carry-forward); a changed
  profile drops the entry, so normalization never vouches for an edit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from server.app.skills.config import LATEST_REF
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict
from server.app.workflows.schema import WorkflowNode, WorkflowNodeSkill
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node

PROVENANCE_KEY = "agent_profile_provenance"

ProfileProvenance = Mapping[str, Mapping[str, Any]]


def provenance_from_payload(payload: Any) -> dict[str, dict[str, Any]]:
    """The well-formed provenance entries of a stored revision payload."""
    raw = payload.get(PROVENANCE_KEY) if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(entry)
        for key, entry in raw.items()
        if isinstance(entry, dict)
        and isinstance(entry.get("definition_hash"), str)
        and isinstance(entry.get("restore"), dict)
    }


def provenance_from_revision_json(definition_json: str | None) -> dict[str, dict[str, Any]]:
    """Provenance of a stored ``definition_json``; corrupt text reads as none."""
    if not definition_json:
        return {}
    try:
        return provenance_from_payload(json.loads(definition_json))
    except (TypeError, ValueError):
        return {}


def embed_provenance(definition_json: str, provenance: ProfileProvenance) -> str:
    """*definition_json* with the provenance sibling embedded (empty = unchanged)."""
    if not provenance:
        return definition_json
    payload = json.loads(definition_json)
    payload[PROVENANCE_KEY] = {key: dict(entry) for key, entry in provenance.items()}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_fields(node: WorkflowNode) -> tuple[Any, ...]:
    return (
        node.execution.runtime,
        sorted(node.requires_labels.items()),
        tuple(node.tools),
        json.dumps(node.config_schema, sort_keys=True),
        (node.skill.key, node.skill.ref) if node.skill is not None else None,
    )


def carry_forward_provenance(
    previous_definition_json: str | None, definition: WorkflowDefinition
) -> dict[str, dict[str, Any]]:
    """Entries of the previous active revision still true for *definition*.

    Kept only for self-contained agent nodes whose profile fields equal the
    previous revision's node (anything else may have been edited after
    inlining). A previous revision that no longer loads carries nothing.
    """
    provenance = provenance_from_revision_json(previous_definition_json)
    if not provenance:
        return {}
    try:
        previous = workflow_definition_from_dict(json.loads(str(previous_definition_json)))
    except ValueError:
        return {}
    kept: dict[str, dict[str, Any]] = {}
    for key, entry in provenance.items():
        node = definition.nodes.get(key)
        before = previous.nodes.get(key)
        if node is None or before is None or not is_self_contained_agent_node(node):
            continue
        if _profile_fields(node) == _profile_fields(before):
            kept[key] = entry
    return kept


def legacy_view(node: WorkflowNode, entry: Mapping[str, Any]) -> WorkflowNode | None:
    """*node* with its profile fields put back to the pre-inlining values."""
    restore = entry.get("restore")
    if not isinstance(restore, dict):
        return None
    raw_skill = restore.get("skill")
    skill = None
    if isinstance(raw_skill, dict) and isinstance(raw_skill.get("key"), str):
        skill = WorkflowNodeSkill(
            key=raw_skill["key"], ref=str(raw_skill.get("ref") or "") or LATEST_REF
        )
    try:
        return replace(
            node,
            execution=replace(node.execution, runtime=str(restore.get("runtime") or "")),
            requires_labels=dict(restore.get("requires_labels") or {}),
            tools=tuple(restore.get("tools") or ()),
            config_schema=dict(restore.get("config_schema") or {}),
            skill=skill,
        )
    except (TypeError, ValueError):
        return None


def upgrade_legacy_views(
    old_definition: WorkflowDefinition,
    new_definition: WorkflowDefinition,
    provenance: ProfileProvenance,
) -> dict[str, WorkflowNode]:
    """node_key → legacy view of the new node, for old legacy / new inlined pairs."""
    views: dict[str, WorkflowNode] = {}
    for key, entry in provenance.items():
        node = new_definition.nodes.get(key)
        old = old_definition.nodes.get(key)
        if node is None or old is None or old.node_type != "agent":
            continue
        if is_self_contained_agent_node(old) or not is_self_contained_agent_node(node):
            continue
        view = legacy_view(node, entry)
        if view is not None:
            views[key] = view
    return views
