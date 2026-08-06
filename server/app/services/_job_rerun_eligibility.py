"""Read-only rerun eligibility helpers shared by execution and preview.

Everything here is side-effect free: the real rerun paths call these checks
before mutating, and the batch-rerun preview counts with the same rules, so
the two can never drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from server.app.services.job_operation_error import JobOperationError
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.workflow_branching import upstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


def check_rerun_eligibility(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
) -> JobOperationError | None:
    """Read-only rerun precheck, shared by ``execute_rerun`` and the preview.

    Covers everything before the mutation: the node exists in the workflow
    definition and for the job, no active executor lease, no running nodes.
    Keeping one implementation prevents the preview count from drifting away
    from what the real rerun would do.
    """
    definition = definition_from_job_snapshot(job) or service.workflows.definition(
        str(job["workflow_key"])
    )
    if actual_node_key not in definition.nodes:
        return JobOperationError(
            job_id,
            "rerun",
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found in workflow",
        )

    if service.job_db.get_job_node(job_id, actual_node_key) is None:
        return JobOperationError(
            job_id,
            "rerun",
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found for job",
        )

    if service.lease_repo.has_active_for_node(job_id, actual_node_key, service._now()):
        return JobOperationError(
            job_id,
            "rerun",
            "skipped",
            actual_node_key,
            "busy",
            "Node has an active executor lease",
        )

    if service._job_has_running_nodes(job_id):
        return JobOperationError(
            job_id,
            "rerun",
            "skipped",
            actual_node_key,
            "busy",
            "Job has running nodes",
        )
    return None


def failed_nodes_by_job(
    service: JobRerunService,
    workspace_id: str,
    category: str,
    requested: Sequence[str],
    workflow_key: str | None = None,
) -> dict[str, list[str]]:
    """Group the latest failed node runs of one category by job, restricted to
    ``requested`` (empty = unrestricted). Shared by the real rerun and the
    preview count so both see the same matching set."""
    runs = service.job_db.list_failed_node_runs(
        workspace_id,
        category=category,
        workflow_key=workflow_key,
        job_ids=requested or None,
    )
    allowed = set(requested)
    failed_nodes_by_job: dict[str, list[str]] = {}
    for run in runs:
        job_id = str(run["job_id"])
        if allowed and job_id not in allowed:
            continue
        nodes = failed_nodes_by_job.setdefault(job_id, [])
        node_key = str(run["node_key"])
        if node_key not in nodes:
            nodes.append(node_key)
    return failed_nodes_by_job


def resolve_failure_rerun_targets(
    definition: Any, failed_nodes: list[str], strategy: str
) -> list[str]:
    """Target nodes for the category strategy (rerun_self / rerun_upstream)."""
    targets: list[str] = []
    for node_key in failed_nodes:
        resolved = upstream_nodes(definition, node_key) if strategy == "rerun_upstream" else []
        # A node without upstreams is rerun itself: it is the root candidate.
        for target in resolved or [node_key]:
            if target not in targets:
                targets.append(target)
    return targets
