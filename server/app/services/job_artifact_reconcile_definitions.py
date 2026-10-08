"""Per-pass definition cache for the artifact reconciler (#714).

Split out of ``job_artifact_maintenance.py`` (file-size budget). The parse
and fallback callables are injected so the maintenance module stays the
single place that names them (and tests keep patching them there).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.jobs import JobQueries


class ReconcileDefinitions:
    """Snapshot parse keyed by its raw text, active-revision fallback keyed
    by workspace — one revision read per distinct workspace per pass instead
    of one per job. A cached ``per_job_failures`` outcome replays as the same
    per-job exception; anything outside that family propagates uncached."""

    def __init__(
        self,
        job_db: JobQueries,
        *,
        from_snapshot: Callable[[dict[str, Any]], Any],
        from_workspace: Callable[[JobQueries, str, str], Any],
        per_job_failures: tuple[type[Exception], ...],
    ) -> None:
        self._job_db = job_db
        self._from_snapshot = from_snapshot
        self._from_workspace = from_workspace
        self._per_job_failures = per_job_failures
        self._snapshots: dict[str, Any] = {}
        self._workspaces: dict[str, Any] = {}

    def for_job(self, job: dict[str, Any]) -> Any:
        raw = str(job.get("workflow_definition_snapshot_json") or "")
        if raw:
            if raw not in self._snapshots:
                self._snapshots[raw] = self._from_snapshot(job)
            snapshot = self._snapshots[raw]
        else:
            snapshot = self._from_snapshot(job)
        if snapshot is not None:
            return snapshot
        workspace_id = str(job["workspace_id"])
        if workspace_id not in self._workspaces:
            try:
                self._workspaces[workspace_id] = self._from_workspace(
                    self._job_db, workspace_id, workspace_id
                )
            except self._per_job_failures as exc:
                self._workspaces[workspace_id] = exc
        outcome = self._workspaces[workspace_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
