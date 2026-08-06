"""Pure bulk-data predicates for the batch-rerun preview.

Each function is the in-memory equivalent of one per-job check on the write
path (``resolve_rerun_node`` / ``check_rerun_eligibility``), evaluated against
bulk-fetched rows so the preview never re-implements an eligibility rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


def resolve_node_key_from_nodes(
    job: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_key: str | None,
    from_failed_node: bool,
) -> str | None:
    """Bulk-data equivalent of ``resolve_rerun_node`` (None == its skip errors)."""
    if from_failed_node:
        if job.get("status") != "failed":
            return None
        for node in nodes:
            if node["status"] == "failed":
                return str(node["node_key"])
        return None
    return node_key


def rerun_eligible_from_nodes(
    definition: Any,
    nodes: list[dict[str, Any]],
    busy_pairs: set[tuple[str, str]],
    job_id: str,
    actual_node_key: str,
) -> bool:
    """Bulk-data equivalent of ``check_rerun_eligibility`` (same four rules)."""
    if actual_node_key not in definition.nodes:
        return False
    if not any(node["node_key"] == actual_node_key for node in nodes):
        return False
    if (job_id, actual_node_key) in busy_pairs:
        return False
    return not any(node["status"] == "running" for node in nodes)
