"""Failed-upstream guard for rerun target selection.

A rerun only resets the target node and its downstream; a failed ancestor
stays failed, and the scheduler requires every upstream to be completed —
so a target with a failed ancestor could never become ready and the job
would sit queued forever (``queued`` job + ``failed`` node). Both the
per-job write path (``check_rerun_eligibility``) and the bulk-data batch
path (``rerun_ineligible_from_nodes``) reject such targets with the exact
same error built here, keeping the two paths equivalent by construction.
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


def upstream_failed_error(job_id: str, node_key: str, failed_keys: list[str]) -> JobOperationError:
    return JobOperationError(
        job_id,
        "rerun",
        "skipped",
        node_key,
        "upstream_failed",
        f"Upstream node(s) failed: {', '.join(failed_keys)}; rerun from the failed node instead",
    )
