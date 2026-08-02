"""Batch rerun of failed nodes selected by failure category."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services._job_rerun_single import execute_rerun_result
from server.app.services.job_selection_resolver import resolve_batch_selection
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.workflow_branching import upstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService

# Hard-coded stage-1/2 defaults for strategy="auto"; workflow-declared
# policies arrive with stage 3.
_AUTO_STRATEGIES = {
    "technical": "rerun_self",
    "business": "rerun_upstream",
    "unknown": "rerun_self",
}


def rerun_by_failure_category(
    service: JobRerunService,
    workspace_id: str,
    category: str,
    *,
    strategy: str = "auto",
    job_ids: list[str] | tuple[str, ...] = (),
    workflow_key: str | None = None,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Rerun the latest failed node runs of one category, one result per job."""
    resolved = _AUTO_STRATEGIES.get(category, "rerun_self") if strategy == "auto" else strategy
    runs = service.job_db.list_failed_node_runs(
        workspace_id, category=category, workflow_key=workflow_key
    )
    ids = resolve_batch_selection(service.job_db, workspace_id, job_ids, job_filter, exclude_ids)
    requested = [value.strip() for value in ids if value.strip()]
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

    results = [
        _rerun_job_failures(service, workspace_id, job_id, nodes, resolved)
        for job_id, nodes in failed_nodes_by_job.items()
    ]
    for job_id in requested:
        if job_id not in failed_nodes_by_job:
            results.append(
                _job_result(
                    job_id,
                    "skipped",
                    "no_matching_failure",
                    f"No latest failed run with category {category!r}",
                )
            )
    return results


def _rerun_job_failures(
    service: JobRerunService,
    workspace_id: str,
    job_id: str,
    failed_nodes: list[str],
    strategy: str,
) -> dict[str, Any]:
    job = service.job_db.get_job(job_id)
    if job is None:
        return _job_result(job_id, "failed", "not_found", "Job not found")
    if job["workspace_id"] != workspace_id:
        return _job_result(
            job_id, "failed", "wrong_workspace", f"Job does not belong to workspace {workspace_id}"
        )

    definition = definition_from_job_snapshot(job) or service.workflows.definition(
        str(job["workflow_key"])
    )
    targets: list[str] = []
    for node_key in failed_nodes:
        resolved = upstream_nodes(definition, node_key) if strategy == "rerun_upstream" else []
        # A node without upstreams is rerun itself: it is the root candidate.
        for target in resolved or [node_key]:
            if target not in targets:
                targets.append(target)

    node_results = [execute_rerun_result(service, job, job_id, target) for target in targets]
    rerun_nodes = [str(r["node_key"]) for r in node_results if r["status"] == "succeeded"]
    failures = [r for r in node_results if r["status"] == "failed"]
    skips = [r for r in node_results if r["status"] == "skipped"]
    result = _job_result(job_id, "succeeded", None, None)
    if failures:
        result["status"] = "failed"
        result["reason_code"] = failures[0]["reason_code"]
        result["message"] = failures[0]["message"]
    elif skips and not rerun_nodes:
        result["status"] = "skipped"
        result["reason_code"] = skips[0]["reason_code"]
        result["message"] = skips[0]["message"]
    result["rerun_nodes"] = rerun_nodes
    return result


def _job_result(
    job_id: str,
    status: str,
    reason_code: str | None,
    message: str | None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": "rerun",
        "status": status,
        "node_key": None,
        "reason_code": reason_code,
        "message": message,
        "rerun_nodes": [],
    }
