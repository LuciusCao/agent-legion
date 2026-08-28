"""Failed-upstream guard for rerun / run-to start-node selection.

Rerun / run-to-with-start only reset the start node and its downstream; a
failed ancestor stays failed and the scheduler requires completed upstreams,
so the start node could never become ready — the job would sit queued
forever with a failed node. Shared by ``check_rerun_eligibility``,
``rerun_ineligible_from_nodes`` and run-to-with-start so every entry point
rejects such selections with the same error.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_operation_error import JobOperationError
from server.app.workflows.workflow_branching import upstream_nodes


def failed_upstream_node_keys(
    definition: Any, nodes: list[dict[str, Any]], node_key: str
) -> list[str]:
    """Failed ancestors of ``node_key`` that a rerun would leave behind."""
    statuses = {str(node["node_key"]): str(node["status"]) for node in nodes}
    failed: list[str] = []
    seen: set[str] = set()
    stack = upstream_nodes(definition, node_key)
    while stack:
        parent = stack.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        if statuses.get(parent) == "failed":
            failed.append(parent)
        stack.extend(upstream_nodes(definition, parent))
    return failed


def upstream_failed_error(
    job_id: str, node_key: str, failed_keys: list[str], *, operation: str = "rerun"
) -> JobOperationError:
    names = ", ".join(failed_keys)
    detail = f"Upstream node(s) failed: {names}; rerun from the failed node instead"
    return JobOperationError(job_id, operation, "skipped", node_key, "upstream_failed", detail)


def raise_if_failed_upstream(
    definition: Any,
    nodes: list[dict[str, Any]],
    start_node_key: str,
    job_id: str,
    operation: str,
    error_node_key: str,
) -> None:
    """Raise-variant for raise-style services; ``error_node_key`` is the
    operation's own key (run-to reports the target, not the start)."""
    failed = failed_upstream_node_keys(definition, nodes, start_node_key)
    if failed:
        raise upstream_failed_error(job_id, error_node_key, failed, operation=operation)
