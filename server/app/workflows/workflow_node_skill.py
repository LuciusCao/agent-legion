"""Node-level skill binding (issue #76, definition layer; #322 ref model).

An Agent-routed node may pin the skill content it runs against: ``key`` is
the two-segment skill key (``<group>/<name>`` under the skill root) and
``ref`` the git ref to run. An empty ref normalizes to ``latest`` (#322):
follow the skill repo's live HEAD, never frozen into the lock; any other
ref (a tag) freezes into the lock on first dispatch. The binding versions
with the workflow revision snapshot (asdict), like ``config_schema``.
Dispatch resolves the effective skill through ``effective_node_skill``
(node binding wins, the Agent definition's skill is the legacy fallback).
"""

from __future__ import annotations

from typing import Any

from server.app.skills.config import LATEST_REF
from server.app.workflows.schema import (
    WorkflowDefinitionError,
    WorkflowNode,
    WorkflowNodeSkill,
)


def load_node_skill(raw_node: dict[str, Any], node_key: str) -> WorkflowNodeSkill | None:
    """Parse the optional ``skill:`` block of a node (string or mapping form)."""
    raw_skill = raw_node.get("skill")
    if raw_skill is None:
        return None
    key: Any
    if isinstance(raw_skill, str):
        key, ref = raw_skill, ""
    elif isinstance(raw_skill, dict):
        unknown = sorted(set(raw_skill) - {"key", "ref"})
        if unknown:
            raise WorkflowDefinitionError(
                f"Node {node_key}.skill only accepts ['key', 'ref']; unknown: {', '.join(unknown)}"
            )
        key = raw_skill.get("key")
        ref = raw_skill.get("ref", "")
        if not isinstance(ref, str):
            raise WorkflowDefinitionError(f"Node {node_key}.skill.ref must be a string")
    else:
        raise WorkflowDefinitionError(
            f"Node {node_key}.skill must be a skill key string or a mapping"
        )
    if not isinstance(key, str) or not key:
        raise WorkflowDefinitionError(f"Node {node_key}.skill.key must be a non-empty string")
    _validate_skill_key(key, node_key)
    # #322: an empty ref is "follow the repo's live HEAD", spelled ``latest``.
    return WorkflowNodeSkill(key=key, ref=ref or LATEST_REF)


def _validate_skill_key(skill_key: str, node_key: str) -> None:
    """Same key rules as SkillManager._parse_skill_key (an instance method on
    the runtime manager; this loader-side copy keeps the import graph acyclic)."""
    if skill_key.startswith("/"):
        raise WorkflowDefinitionError(f"Node {node_key}.skill key must be relative: {skill_key!r}")
    parts = skill_key.split("/")
    if ".." in parts:
        raise WorkflowDefinitionError(
            f"Node {node_key}.skill key must not contain '..': {skill_key!r}"
        )
    if len(parts) != 2 or not all(parts):
        raise WorkflowDefinitionError(
            f"Node {node_key}.skill key must be <group>/<name>: {skill_key!r}"
        )


def apply_skill_echo(raw_node: dict[str, Any], node: WorkflowNode) -> None:
    """Echo the node's skill binding in yaml form; omitted when unset.

    The loader normalized the ref (never empty), so the echo is always the
    mapping form. Start/approval nodes never carry a binding (loader-forbidden).
    """
    if node.skill is None:
        return
    raw_node["skill"] = {"key": node.skill.key, "ref": node.skill.ref or LATEST_REF}


def effective_node_skill(node: WorkflowNode, agent_skill: str) -> tuple[str, str]:
    """Dispatch-time (key, ref): the node binding wins; the Agent definition's
    skill is the legacy fallback (ref-less, hence ``latest``). Raises
    ValueError when neither names one."""
    if node.skill is not None:
        return node.skill.key, node.skill.ref or LATEST_REF
    if agent_skill:
        return agent_skill, LATEST_REF
    raise ValueError(f"Agent node {node.key} has no skill on node or Agent definition")


def node_skill_publish_error(node: WorkflowNode, agent_skill: str | None) -> str | None:
    """Publish-gate check for a node's skill binding.

    ``agent_skill``: the resolved published Agent's skill for the node's
    capability; ``None`` for code-routed nodes and agent nodes without a
    single published Agent. Agent-routed: the binding may live on the node
    or (legacy) on the Agent definition — at least one side must name one.
    Code-routed: a declared skill is meaningless (never runs skill content).
    The #322 repo-existence check lives in ``workflows/skill_repo_gate``.
    """
    if agent_skill is None:
        if node.skill is not None:
            return (
                f"Node {node.key} declares a skill but capability {node.capability} resolves "
                "to no published Agent; skill only applies to Agent-routed nodes"
            )
        return None
    if node.skill is None and not agent_skill:
        return (
            f"Agent node {node.key} declares no skill and the published Agent for "
            f"capability {node.capability} has none either: declare skill "
            "(key + ref) on the node, or keep a skill on the Agent definition"
        )
    return None
