"""Approval node: a human decision gate inside the DAG (EXEC-APPROVAL-001).

A ``type: approval`` node never dispatches to an executor. When the
scheduler finds it ready (all upstream completed, declared inputs present)
the claim path parks its job_node at ``awaiting_approval`` instead of
claiming a lease; only a human decision through the approval API moves it
on — ``approved`` completes the node (unblocking downstream), ``rework``
resets an upstream node with the reviewer's feedback written as an
artifact, ``rejected`` fails it. Everything downstream simply waits on the
node becoming ``completed``, so the scheduler core needs no special case
beyond the park.

The node's ``inputs`` double as the review material list shown to the
reviewer. Like a start node it declares no capability and no execution
fields; unlike a start node it *does* enter job_nodes and supports
``config`` (``rework_target`` — default upstream node reset by a rework
decision — and ``feedback_artifact`` — filename the reviewer note is
written to, default ``review_feedback.json``).
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError

APPROVAL_NODE_TYPE = "approval"

#: job_nodes.status while the gate waits for a human decision. Deliberately
#: outside RUNNABLE_STATUSES and outside the terminal-success set: the node
#: can neither be claimed again nor unblock downstream until decided.
AWAITING_APPROVAL_STATUS = "awaiting_approval"

#: Decision verdicts accepted by the approval API (insert-only audit rows).
APPROVAL_VERDICTS = ("approved", "rework", "rejected")

DEFAULT_FEEDBACK_ARTIFACT = "review_feedback.json"

# Execution fields are meaningless on a node that never dispatches; reject
# them at load time instead of letting them sit silently inert.
_FORBIDDEN_APPROVAL_FIELDS = ("capability", "execution", "shard", "reduce", "config_schema")

_ALLOWED_CONFIG_KEYS = ("rework_target", "feedback_artifact")


def validate_approval_fields(raw_node: dict[str, Any], node_key: str) -> None:
    """Enforce the approval-node field rules at definition load time."""
    for forbidden in _FORBIDDEN_APPROVAL_FIELDS:
        if forbidden in raw_node:
            raise WorkflowDefinitionError(f"Approval node {node_key} must not declare {forbidden}")
    raw_config = raw_node.get("config") or {}
    if not isinstance(raw_config, dict):
        return  # the generic loader rejects non-mapping configs with its own error
    unknown = sorted(set(raw_config) - set(_ALLOWED_CONFIG_KEYS))
    if unknown:
        raise WorkflowDefinitionError(
            f"Approval node {node_key}.config only accepts"
            f" {list(_ALLOWED_CONFIG_KEYS)}; unknown: {', '.join(unknown)}"
        )
    rework_target = raw_config.get("rework_target")
    if rework_target is not None and (not isinstance(rework_target, str) or not rework_target):
        raise WorkflowDefinitionError(
            f"Approval node {node_key}.config.rework_target must be a non-empty string"
        )
    feedback = raw_config.get("feedback_artifact")
    if feedback is not None and (
        not isinstance(feedback, str) or not feedback or "/" in feedback or "\\" in feedback
    ):
        raise WorkflowDefinitionError(
            f"Approval node {node_key}.config.feedback_artifact must be a bare filename"
        )


def validate_approval_edges(nodes: dict[str, Any], edges: list[Any]) -> None:
    """Approval nodes must sit inside the graph: at least one incoming edge.

    A gate with nothing upstream would park immediately at job start with
    nothing to review; reject it at load time. (No outgoing edge is legal —
    a final sign-off gate before packaging is a real shape.)
    """
    approval_keys = {
        key for key, node in nodes.items() if getattr(node, "node_type", "") == APPROVAL_NODE_TYPE
    }
    if not approval_keys:
        return
    # Edges from the start node don't count: start never executes, so a gate
    # fed only by start would still have nothing to review (the loader may
    # have injected that edge synthetically for rootless nodes).
    start_keys = {key for key, node in nodes.items() if getattr(node, "node_type", "") == "start"}
    targets = {edge.target for edge in edges if edge.source not in start_keys}
    for key in sorted(approval_keys - targets):
        raise WorkflowDefinitionError(
            f"Approval node {key} must have at least one incoming edge from an executable node"
        )


def strip_snapshot_placeholders(raw_node: dict[str, Any]) -> None:
    """Drop the per-type placeholder fields an asdict snapshot carries.

    Snapshots serialize every dataclass field on every node. A start node
    must not declare any execution/config field; an approval node must not
    declare execution fields but keeps ``config``/``terminal``; every other
    node drops the default ``accepted_item_types`` copy (start-only). For
    approval nodes the execution placeholder serializes as a dict of empty
    strings, so "empty" means every value falsy, not the container itself.
    """
    node_type = raw_node.get("type")
    if node_type == "start":
        for placeholder in (
            "capability",
            "execution",
            "shard",
            "reduce",
            "terminal",
            "config",
            "config_schema",
        ):
            raw_node.pop(placeholder, None)
        return
    raw_node.pop("accepted_item_types", None)
    if node_type != APPROVAL_NODE_TYPE:
        return
    for placeholder in ("capability", "execution", "shard", "reduce", "config_schema"):
        value = raw_node.get(placeholder)
        if (isinstance(value, dict) and not any(value.values())) or not value:
            raw_node.pop(placeholder, None)


def approval_rework_target(node: Any) -> str:
    """The node's declared default rework target ('' when undeclared)."""
    value = (node.config or {}).get("rework_target", "")
    return value if isinstance(value, str) else ""


def approval_feedback_artifact(node: Any) -> str:
    """The filename rework feedback is written to for this gate."""
    value = (node.config or {}).get("feedback_artifact", "")
    if isinstance(value, str) and value:
        return value
    return DEFAULT_FEEDBACK_ARTIFACT
