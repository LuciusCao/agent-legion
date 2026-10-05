"""Self-contained agent node profile fields: loading and per-node rules (#933).

#440 P2 (D5): an ``agent`` node may carry its own execution profile instead
of resolving a published Agent definition by capability —

- ``execution.runtime`` (one of ``AGENT_RUNTIMES``; the workflow top-level
  ``execution.runtime`` is the default, merged in by
  ``workflow_execution_defaults``);
- node top-level ``requires_labels`` (Worker labels ⊇ these at claim);
- the existing node ``tools`` / ``config_schema`` / ``skill`` fields.

A node is self-contained once its effective runtime is non-empty. Only
agent nodes may declare either field. ``requires_labels`` without a runtime
is a half-filled profile: the loader accepts it (snapshots must reload) and
the publish gate rejects it (``agent_node_profile.node_profile_error``) so a
node never mixes node-declared labels with a definition-sourced profile.
"""

from __future__ import annotations

from typing import Any

from server.app.agent_runtime.catalog import AGENT_RUNTIMES
from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNode


def validate_agent_runtime(runtime: str, where: str) -> None:
    """Empty (undeclared) or one of the catalog runtimes; anything else raises."""
    if runtime and runtime not in AGENT_RUNTIMES:
        raise WorkflowDefinitionError(
            f"{where} must be one of {', '.join(AGENT_RUNTIMES)} (got {runtime!r})"
        )


def load_node_requires_labels(raw_node: dict[str, Any], node_key: str) -> dict[str, str]:
    """Node top-level ``requires_labels``: a mapping of non-empty string keys to strings."""
    raw = raw_node.get("requires_labels")
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) for key, value in raw.items()
    ):
        raise WorkflowDefinitionError(
            f"Node {node_key}.requires_labels must be a mapping of non-empty string keys"
            " to string values"
        )
    return dict(raw)


def validate_node_profile_fields(node: WorkflowNode) -> None:
    """Per-node rules once the type is known: runtime valid, both fields agent-only."""
    validate_agent_runtime(node.execution.runtime, f"Node {node.key}.execution.runtime")
    if node.node_type == "agent":
        return
    if node.execution.runtime:
        raise WorkflowDefinitionError(
            f"Node {node.key}.execution.runtime is only valid on an agent node"
        )
    if node.requires_labels:
        raise WorkflowDefinitionError(
            f"Node {node.key}.requires_labels is only valid on an agent node"
        )


def is_self_contained_agent_node(node: Any) -> bool:
    """True when an agent node carries its own execution profile (runtime declared)."""
    if getattr(node, "node_type", "") != "agent":
        return False
    execution = getattr(node, "execution", None)
    return bool(getattr(execution, "runtime", ""))
