"""Per-node rules of the schema v93 Agent profile backfill (#935, #440 P3 §3).

Pure (no DB): resolve which published Agent definition a legacy agent node
runs today, and inline that definition into the node as its self-contained
execution profile. Split from ``agent_profile_backfill`` (the DB walk) so the
rules are testable on plain dicts and the walk stays small.

Resolution mirrors dispatch, not a forecast:

* active revision node with a materialized ``workspace_node_routes`` row →
  the route target's published version (dispatch errors on an unpublished
  target or a capability mismatch, so those stay unresolved);
* active revision node without a route row → its capability's unique
  published Agent (``workflow_worker/routing_fallback``);
* draft node → its capability's unique published Agent (what the next
  publish would have routed to).

Backfill rules (#440 §3): ``runtime`` / ``requires_labels`` copied;
``tools`` only when the node declares none; ``config_schema`` overwritten
(an agent node's own declaration was always ignored); ``skill`` sinks only
when the node binds none and the definition has one in the two-segment
``<group>/<name>`` shape the loader accepts — otherwise the node stays
untouched (``skill_unportable``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The five node fields the backfill may write (``restore`` keeps their
#: pre-backfill values in the provenance, for the upgrade diff, #935 S2).
PROFILE_FIELDS = ("runtime", "requires_labels", "tools", "config_schema", "skill")


@dataclass(frozen=True)
class PublishedAgent:
    """One published Agent version, validated through ``AgentDefinition``."""

    agent_id: str
    version: int
    capability: str
    runtime: str
    tools: tuple[str, ...]
    requires_labels: Mapping[str, str]
    config_schema: Mapping[str, Any]
    skill: str
    #: ``AgentDefinition.definition_hash()`` — the identity dispatch records
    #: (``node_runs.agent_definition_hash``), not the stored column.
    definition_hash: str


@dataclass
class LegacyCatalog:
    """A workspace's Agent rows: published versions + every agent's latest status."""

    published: dict[str, PublishedAgent] = field(default_factory=dict)
    #: agent_id → (capability, status) of the newest version, any status.
    latest: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: agent ids whose published definition no longer validates.
    invalid: set[str] = field(default_factory=set)

    def candidates(self, capability: str) -> list[str]:
        return sorted(a for a, agent in self.published.items() if agent.capability == capability)


@dataclass(frozen=True)
class Resolution:
    agent: PublishedAgent | None
    reason: str = ""
    agent_ids: tuple[str, ...] = ()


def _unresolved_by_capability(capability: str, catalog: LegacyCatalog) -> Resolution:
    candidates = catalog.candidates(capability)
    if len(candidates) > 1:
        return Resolution(None, "ambiguous", tuple(candidates))
    matching = {a: s for a, (cap, s) in catalog.latest.items() if cap == capability}
    for reason in ("archived", "draft"):
        ids = tuple(sorted(a for a, s in matching.items() if s == reason))
        if ids:
            return Resolution(None, "draft_only" if reason == "draft" else reason, ids)
    invalid = tuple(
        sorted(a for a in catalog.invalid if catalog.latest.get(a, ("",))[0] == capability)
    )
    if invalid:
        return Resolution(None, "definition_invalid", invalid)
    return Resolution(None, "no_agent")


def resolve_by_capability(capability: str, catalog: LegacyCatalog) -> Resolution:
    candidates = catalog.candidates(capability)
    if len(candidates) == 1:
        return Resolution(catalog.published[candidates[0]])
    return _unresolved_by_capability(capability, catalog)


def resolve_routed(capability: str, route_target: str | None, catalog: LegacyCatalog) -> Resolution:
    """Active-revision resolution: the route target when routed, else capability."""
    if route_target is None:
        return resolve_by_capability(capability, catalog)
    agent = catalog.published.get(route_target)
    if agent is None:
        status = catalog.latest.get(route_target, ("", "missing"))[1]
        reason = "archived" if status == "archived" else "route_target_unpublished"
        if route_target in catalog.invalid:
            reason = "definition_invalid"
        return Resolution(None, reason, (route_target,))
    if agent.capability != capability:
        return Resolution(None, "route_capability_mismatch", (route_target,))
    return Resolution(agent)


def skill_key_portable(key: str) -> bool:
    """The loader's node skill key rule (``<group>/<name>``, relative, no ``..``)."""
    parts = key.split("/")
    return not key.startswith("/") and ".." not in parts and len(parts) == 2 and all(parts)


def effective_runtime(node: Mapping[str, Any], top_execution: Any) -> str:
    """Node ``execution.runtime``, else the workflow top-level default."""
    execution = node.get("execution")
    runtime = execution.get("runtime") if isinstance(execution, dict) else ""
    if not runtime and isinstance(top_execution, dict):
        runtime = top_execution.get("runtime") or ""
    return str(runtime or "")


def _skill_absent(node: Mapping[str, Any]) -> bool:
    return node.get("skill") in (None, "", {})


def backfill_blocker(node: Mapping[str, Any], agent: PublishedAgent) -> str:
    """Why this node cannot be inlined from *agent* (``""`` = it can)."""
    if not isinstance(node.get("execution", {}) or {}, dict):
        return "malformed_execution"
    if _skill_absent(node) and agent.skill and not skill_key_portable(agent.skill):
        return "skill_unportable"
    return ""


def backfill_revision_node(node: dict[str, Any], agent: PublishedAgent) -> dict[str, Any]:
    """Inline *agent* into one revision (asdict-shaped) node; returns ``restore``."""
    execution = dict(node.get("execution") or {})
    restore = {
        "runtime": str(execution.get("runtime") or ""),
        "requires_labels": dict(node.get("requires_labels") or {}),
        "tools": list(node.get("tools") or []),
        "config_schema": dict(node.get("config_schema") or {}),
        "skill": node.get("skill") if not _skill_absent(node) else None,
    }
    execution["runtime"] = agent.runtime
    node["execution"] = execution
    node["requires_labels"] = dict(agent.requires_labels)
    if not node.get("tools"):
        node["tools"] = list(agent.tools)
    node["config_schema"] = dict(agent.config_schema)
    if _skill_absent(node) and agent.skill:
        node["skill"] = {"key": agent.skill, "ref": "latest"}
    return restore


def backfill_draft_node(node: dict[str, Any], agent: PublishedAgent) -> None:
    """Inline *agent* into one draft YAML node, keeping the YAML spelling sparse.

    Empty values are omitted the way ``definition_to_yaml`` echoes them
    (no ``requires_labels: {}`` / ``config_schema: {}`` noise).
    """
    execution = node.get("execution")
    execution = dict(execution) if isinstance(execution, dict) else {}
    execution["runtime"] = agent.runtime
    node["execution"] = execution
    if agent.requires_labels:
        node["requires_labels"] = dict(agent.requires_labels)
    else:
        node.pop("requires_labels", None)
    if not node.get("tools"):
        node["tools"] = list(agent.tools)
    if agent.config_schema:
        node["config_schema"] = dict(agent.config_schema)
    else:
        node.pop("config_schema", None)
    if _skill_absent(node) and agent.skill:
        node["skill"] = {"key": agent.skill, "ref": "latest"}


def provenance_entry(agent: PublishedAgent, restore: dict[str, Any]) -> dict[str, Any]:
    """``agent_profile_provenance[node_key]`` (outside ``definition_hash``)."""
    return {
        "agent_id": agent.agent_id,
        "version": agent.version,
        "definition_hash": agent.definition_hash,
        "restore": restore,
    }
