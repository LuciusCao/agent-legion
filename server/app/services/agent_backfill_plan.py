"""Per-node backfill simulation for the Agent retirement dry-run (#934, #440 §3).

Pure (no DB): given one parsed workflow definition and the workspace's
legacy Agent catalog, simulate the 0.7.17 backfill rules through the P1
profile facade (``resolve_agent_node_profile`` + capability index) — this
module never re-implements resolution:

* runtime / requires_labels: copied from the definition;
* tools: the node's own non-empty list wins, otherwise the definition's;
* config_schema: overwritten by the definition's (agent nodes' own
  declarations were always ignored) — differences are reported;
* skill: the node binding wins; otherwise the definition's legacy skill
  sinks to the node (``effective_node_skill``, ref ``latest``).

Nodes whose capability resolves to zero or several published Agents stay
untouched and are reported as unresolved with a reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile import (
    CapabilityIndex,
    legacy_agent_candidates,
    resolve_agent_node_profile,
)
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_skill import effective_node_skill


def _skill_backfill(node: WorkflowNode, definition_skill: str) -> dict[str, Any] | None:
    try:
        key, ref = effective_node_skill(node, definition_skill)
    except ValueError:
        return None
    return {"key": key, "ref": ref, "source": "node" if node.skill is not None else "definition"}


def config_schema_diff(
    node_schema: Mapping[str, Any], backfilled: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Property-level diff of a non-empty node declaration vs the overwrite.

    None when the node declared nothing (the overwrite only fills) or the
    two are equal — only declarations the backfill would discard count.
    """
    if not node_schema or dict(node_schema) == dict(backfilled):
        return None
    node_props = dict(node_schema.get("properties") or {})
    new_props = dict(backfilled.get("properties") or {})
    top_keys = (set(node_schema) | set(backfilled)) - {"properties"}
    return {
        "node_declared": dict(node_schema),
        "backfilled": dict(backfilled),
        "properties_added": sorted(set(new_props) - set(node_props)),
        "properties_removed": sorted(set(node_props) - set(new_props)),
        "properties_changed": sorted(
            k for k in set(node_props) & set(new_props) if node_props[k] != new_props[k]
        ),
        "other_keys_changed": sorted(
            k for k in top_keys if node_schema.get(k) != backfilled.get(k)
        ),
    }


def _unresolved_reason(
    node: WorkflowNode, candidates: Sequence[str], unpublished: Mapping[str, tuple[str, str]]
) -> tuple[str, list[str]]:
    """(reason, agent ids): ambiguous / archived / draft_only / no_agent."""
    if len(candidates) > 1:
        return "ambiguous", sorted(candidates)
    matching = {
        agent_id: status
        for agent_id, (capability, status) in unpublished.items()
        if capability == node.capability
    }
    archived = sorted(a for a, s in matching.items() if s == "archived")
    if archived:
        return "archived", archived
    drafts = sorted(a for a, s in matching.items() if s == "draft")
    if drafts:
        return "draft_only", drafts
    return "no_agent", []


def plan_definition_backfill(
    definition: WorkflowDefinition,
    catalog: Mapping[str, AgentDefinition],
    index: CapabilityIndex,
    unpublished: Mapping[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """One entry per ``type: agent`` node, sorted by node key.

    *unpublished*: agent_id → (capability, latest status) for Agents with
    no published version (classifies unresolved nodes; never resolves).
    Each entry has ``status`` ``backfill`` (with the simulated fields),
    ``unresolved`` (with ``reason`` / ``agent_ids``), or ``self_contained``
    (the profile no longer comes from a definition — nothing to backfill).
    """
    entries: list[dict[str, Any]] = []
    for key in sorted(definition.nodes):
        node = definition.nodes[key]
        if node.node_type != "agent":
            continue
        base = {"node_key": key, "capability": node.capability}
        profile = resolve_agent_node_profile(node, catalog, index=index)
        if profile is None:
            candidates = legacy_agent_candidates(node, catalog, index=index)
            reason, agent_ids = _unresolved_reason(node, candidates, unpublished)
            entries.append(
                {**base, "status": "unresolved", "reason": reason, "agent_ids": agent_ids}
            )
            continue
        legacy = profile.legacy_ref
        if legacy is None:
            # Already self-contained (P2 node source, #933): nothing to backfill.
            entries.append({**base, "status": "self_contained", "source": profile.source})
            continue
        schema = dict(profile.config_schema)
        entries.append(
            {
                **base,
                "status": "backfill",
                "agent_id": legacy.agent_id,
                "agent_definition_hash": legacy.definition_hash(),
                "runtime": profile.runtime,
                "tools": {
                    "value": list(node.tools or profile.tools),
                    "source": "node" if node.tools else "definition",
                },
                "config_schema": schema,
                "config_schema_diff": config_schema_diff(node.config_schema, schema),
                "skill": _skill_backfill(node, profile.skill),
                "requires_labels": dict(sorted(profile.requires_labels.items())),
            }
        )
    return entries
