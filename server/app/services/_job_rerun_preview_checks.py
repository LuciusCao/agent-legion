"""Pure bulk-data predicates for the batch-rerun preview and batch execution.

Each function is the in-memory equivalent of one per-job check on the write
path (``resolve_rerun_node`` / ``check_rerun_eligibility``), evaluated against
bulk-fetched rows so batch paths never re-implement an eligibility rule. The
error-returning variants carry the exact ``JobOperationError`` the per-job
path would raise, keeping batch results identical to per-job ``rerun()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services._job_rerun_upstream_guard import (
    failed_upstream_node_keys,
    upstream_failed_error,
)
from server.app.services.job_operation_error import JobOperationError
from server.app.services.workflow_revision_format import definition_from_job_snapshot

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


class PreviewDefinitions:
    """Workflow definitions cached by workflow_key (a selection usually spans
    one or two workflows); per-job snapshots still win, as in the write path.
    Snapshot payloads are cached by content: jobs of one batch carry the same
    frozen definition, so parsing once per distinct payload is enough."""

    def __init__(self, service: JobRerunService) -> None:
        self._service = service
        self._cache: dict[str, Any] = {}
        self._snapshots: dict[str, Any] = {}

    def for_job(self, job: dict[str, Any]) -> Any:
        raw = str(job.get("workflow_definition_snapshot_json") or "")
        if raw:
            if raw not in self._snapshots:
                self._snapshots[raw] = definition_from_job_snapshot(job)
            snapshot = self._snapshots[raw]
            if snapshot is not None:
                return snapshot
        key = str(job["workflow_key"])
        if key not in self._cache:
            self._cache[key] = self._service.workflows.definition(key)
        return self._cache[key]


def resolve_rerun_node_from_nodes(
    job_id: str,
    job: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_key: str | None,
    from_failed_node: bool,
) -> str:
    """Bulk-data equivalent of ``resolve_rerun_node``: same errors, no queries."""
    if from_failed_node:
        if job.get("status") != "failed":
            raise JobOperationError(
                job_id, "rerun", "skipped", None, "not_failed", "Job is not failed"
            )
        for node in nodes:
            if node["status"] == "failed":
                return str(node["node_key"])
        raise JobOperationError(
            job_id, "rerun", "skipped", None, "no_failed_node", "No failed node found"
        )
    if node_key is None:
        raise JobOperationError(
            job_id, "rerun", "failed", None, "node_key_required", "node_key is required"
        )
    return node_key


def rerun_ineligible_from_nodes(
    definition: Any,
    nodes: list[dict[str, Any]],
    busy_pairs: set[tuple[str, str]],
    job_id: str,
    actual_node_key: str,
) -> JobOperationError | None:
    """Bulk-data equivalent of ``check_rerun_eligibility``: same five rules,
    same errors, no queries."""
    if actual_node_key not in definition.nodes:
        return JobOperationError(
            job_id,
            "rerun",
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found in workflow",
        )
    if not any(node["node_key"] == actual_node_key for node in nodes):
        return JobOperationError(
            job_id,
            "rerun",
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found for job",
        )
    if (job_id, actual_node_key) in busy_pairs:
        return JobOperationError(
            job_id,
            "rerun",
            "skipped",
            actual_node_key,
            "busy",
            "Node has an active executor lease",
        )
    if any(node["status"] == "running" for node in nodes):
        return JobOperationError(
            job_id,
            "rerun",
            "skipped",
            actual_node_key,
            "busy",
            "Job has running nodes",
        )
    failed_upstream = failed_upstream_node_keys(definition, nodes, actual_node_key)
    if failed_upstream:
        return upstream_failed_error(job_id, actual_node_key, failed_upstream)
    return None
