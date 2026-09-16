"""External artifact-access service (#631).

Reads one job's status and artifact manifest through a workspace-prefixed
scope: the workspace binding doubles as the existence check (a job id from
another workspace is a 404, not a 403, so ids cannot be probed across
workspaces). All data access stays behind the JobQueries facade
(BOUNDARY-DATA-001); the listing merges the object-storage manifest (the
authoritative copy, EXEC-ARTIFACT-STORE-001) with legacy local job_dir names.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_artifact_media import raw_media_type
from server.app.services.job_errors import NotFoundError
from server.app.services.job_query_presenters import artifact_names
from server.app.settings import Settings

_JOB_STATUS_FIELDS = (
    "id",
    "workspace_id",
    "status",
    "outcome",
    "created_at",
    "updated_at",
)


class ExternalArtifactAccessService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        object_store: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        # Typed Any like the studio-agent tool surface: the composition root
        # (routes/__init__.py) passes the JobArtifactObjectStore; tests inject
        # fakes exposing enabled/rows_for_job/lookup/open_*.
        self.object_store = object_store

    def _job_in_workspace_or_404(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        # #631: the workspace check doubles as the existence check — a job id
        # from another workspace is a 404 (not 403), so ids cannot be probed
        # across workspaces (same pattern as studio_agent_job_tools).
        job = self.job_db.get_job(job_id)
        if job is None or str(job["workspace_id"]) != workspace_id:
            raise NotFoundError("Job not found")
        return job

    def require_job_in_workspace(self, workspace_id: str, job_id: str) -> None:
        """Guard-only variant for endpoints that read through another service
        (the raw download reuses JobArtifactService.open_raw, which has no
        workspace context of its own)."""
        self._job_in_workspace_or_404(workspace_id, job_id)

    def _object_storage_enabled(self) -> bool:
        return self.object_store is not None and bool(self.object_store.enabled)

    def _enabled_store(self) -> Any | None:
        """The store when object storage is configured (typed local so mypy
        sees the None-guard the way the runtime does)."""
        if self.object_store is None or not self.object_store.enabled:
            return None
        return self.object_store

    def _node_progress(self, job_id: str) -> tuple[int, int, str]:
        nodes = self.job_db.list_job_nodes(job_id)
        completed = sum(1 for node in nodes if str(node.get("status")) == "completed")
        failed = next(
            (
                str(node.get("error_message") or "")[:240]
                for node in nodes
                if str(node.get("status")) == "failed"
            ),
            "",
        )
        return completed, len(nodes), failed

    def status(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        """Lightweight status view: id/scope/status/outcome/timestamps plus
        the artifact name list (external callers poll this, not JobDetail)."""
        job = self._job_in_workspace_or_404(workspace_id, job_id)
        completed, total, error_summary = self._node_progress(job_id)
        return {
            "job_id": str(job["id"]),
            "workspace_id": str(job["workspace_id"]),
            "status": str(job["status"]),
            "outcome": str(job.get("outcome") or ""),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "error_summary": error_summary,
            "completed_nodes": completed,
            "total_nodes": total,
            "artifacts": self._artifact_names(job),
        }

    def _artifact_names(self, job: dict[str, Any]) -> list[str]:
        names = set(artifact_names(job, self.settings))
        # enabled 门控（与 JobQueryService._artifact_names 同语义）：实例
        # 摘掉存储配置后清单里的名字读不到，不再列出。
        store = self._enabled_store()
        if store is not None:
            names |= store.names_for_job(str(job["id"]))
        return sorted(names)

    def list_artifacts(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        """Manifest listing for the job's CURRENT artifacts (rerun semantics
        #508): one entry per name — the manifest row's content_hash/uploaded_at
        identify which execution produced the bytes being served. Jobs that
        are still running list what exists so far (stable: callers poll)."""
        job = self._job_in_workspace_or_404(workspace_id, job_id)
        store = self._enabled_store()
        object_backed: dict[str, dict[str, Any]] = {}
        if store is not None:
            # rows_for_job: the authoritative manifest. A rerun upserts on
            # (job_id, node_key, name), so the latest row per name IS the
            # current execution's copy.
            for row in store.rows_for_job(job_id):
                object_backed[str(row["name"])] = row
        entries: list[dict[str, Any]] = [
            self._entry_from_row(row) for row in object_backed.values()
        ]
        local_names = set(artifact_names(job, self.settings))
        if store is not None:
            local_names -= set(object_backed)
        entries.extend(self._local_entry(name) for name in sorted(local_names))
        return {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": str(job["status"]),
            "artifacts": entries,
            "object_storage_enabled": self._object_storage_enabled(),
        }

    def _entry_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        name = str(row["name"])
        size = row.get("size_bytes")
        return {
            "name": name,
            "storage": "object",
            "node_key": str(row.get("node_key") or ""),
            "size_bytes": int(size) if isinstance(size, int) else None,
            "content_hash": str(row.get("content_hash") or ""),
            "uploaded_at": row.get("uploaded_at"),
            "media_type": raw_media_type(name),
        }

    def _local_entry(self, name: str) -> dict[str, Any]:
        # Legacy job_dir-only artifact (never uploaded): no manifest row, so
        # size/hash are unknown at listing time; the raw endpoint still
        # serves it from the local copy.
        return {
            "name": name,
            "storage": "local",
            "node_key": "",
            "size_bytes": None,
            "content_hash": "",
            "uploaded_at": None,
            "media_type": raw_media_type(name),
        }
