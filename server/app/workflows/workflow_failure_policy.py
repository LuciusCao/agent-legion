"""Node failure declarations and their legacy-compatible rerun defaults."""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNode

FAILURE_POLICY_CATEGORIES = ("technical", "business", "unknown")
RERUN_STRATEGIES = ("rerun_self", "rerun_upstream")
DEFAULT_ON_FAILURE = {
    "technical": "rerun_self",
    "business": "rerun_upstream",
    "unknown": "rerun_self",
}


def load_failure_policy(raw_node: dict[str, Any], node_key: str) -> dict[str, Any]:
    raw_on_failure = raw_node.get("on_failure", {})
    if not isinstance(raw_on_failure, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.on_failure must be a mapping")
    on_failure: dict[str, str] = {}
    for category, strategy in raw_on_failure.items():
        if category not in FAILURE_POLICY_CATEGORIES:
            raise WorkflowDefinitionError(
                f"Node {node_key}.on_failure has unknown category {category!r}"
            )
        if strategy not in RERUN_STRATEGIES:
            raise WorkflowDefinitionError(
                f"Node {node_key}.on_failure.{category} must be one of {RERUN_STRATEGIES}"
            )
        on_failure[str(category)] = str(strategy)
    failure_default = raw_node.get("failure_default", "")
    if not isinstance(failure_default, str) or (
        failure_default and failure_default not in FAILURE_POLICY_CATEGORIES
    ):
        raise WorkflowDefinitionError(
            f"Node {node_key}.failure_default must be one of {FAILURE_POLICY_CATEGORIES}"
        )
    return {"on_failure": on_failure, "failure_default": failure_default}


def resolve_node_rerun_strategy(node: WorkflowNode, category: str) -> str:
    return node.on_failure.get(category, DEFAULT_ON_FAILURE.get(category, "rerun_self"))
