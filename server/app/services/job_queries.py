from typing import Any

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_node_ordering import ordered_job_nodes
from server.app.services.job_node_worker_projection import agent_route_map, claimed_worker_map
from server.app.services.job_patch_query_summaries import summarize_paginated_jobs
from server.app.services.job_path_projection import resolve_record_paths
from server.app.services.job_query_presenters import (
    artifact_names,
    job_nodes_with_definition,
    node_summary,
)
from server.app.services.job_workflow_versions import is_workflow_outdated
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.services.workspace_dag import build_workspace_dag
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition

_RUN_PATH_FIELDS = {"log_path", "run_dir", "session_dir"}
_UNSET: Any = object()


class JobQueryService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workspace_execution_config: WorkspaceExecutionConfigurationService,
        object_store: Any = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.workspace_execution_config = workspace_execution_config
        # D12: artifact listing is the local job_dir ∪ the object-storage
        # manifest (evicted cache entries stay listed).
        self.object_store = object_store

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _definition_for_job(self, job: dict[str, Any]) -> WorkflowDefinition:
        # Snapshot-less jobs fall back to their own workspace's active
        # revision (schema v50), never a global template.
        return definition_from_job_snapshot(job) or require_workspace_active_definition(
            self.job_db, str(job["workspace_id"]), str(job["workflow_key"])
        )

    def _artifact_names(self, job: dict[str, Any]) -> list[str]:
        names = set(artifact_names(job, self.settings))
        # enabled 门控：实例摘掉存储配置后清单里的名字读不到，不再列出。
        if self.object_store is not None and self.object_store.enabled:
            names |= self.object_store.names_for_job(str(job["id"]))
        return sorted(names)

    def _job_summary(
        self,
        job: dict[str, Any],
        nodes: list[dict[str, Any]],
        definition: WorkflowDefinition,
        active_revision: Any = _UNSET,
    ) -> dict[str, Any]:
        ordered_nodes = ordered_job_nodes(nodes, definition)
        summaries = [node_summary(node, definition) for node in ordered_nodes]
        completed_nodes = sum(1 for summary in summaries if summary["status"] == "completed")
        total_nodes = len(summaries)

        active_node_key: str | None = None
        for summary in summaries:
            if summary["status"] == "running":
                active_node_key = summary["node_key"]
                break
        if active_node_key is None:
            for summary in summaries:
                if summary["status"] == "failed":
                    active_node_key = summary["node_key"]
                    break

        error_summary = ""
        if active_node_key is not None:
            active_summary = next(
                (summary for summary in summaries if summary["node_key"] == active_node_key),
                None,
            )
            if active_summary is not None:
                error_summary = active_summary["error_message"][:240]

        if active_revision is _UNSET:
            active = self.job_db.get_active_workflow_revision(
                str(job["workspace_id"]), str(job["workflow_key"])
            )
        else:
            active = active_revision
        job_workflow_version = job.get("workflow_version")
        is_outdated = is_workflow_outdated(job, active)

        job = resolve_record_paths(job, self.settings.data_dir, {"storage_dir"})
        return {
            **job,
            # Wire compatibility: the API field keeps the legacy name while
            # the column is jobs.run_id (schema v53); the value is the run id.
            "batch_id": str(job.get("run_id") or ""),
            "workflow_revision_id": job.get("workflow_revision_id", ""),
            "workflow_version": job_workflow_version,
            "workflow_definition_hash": job.get("workflow_definition_hash", ""),
            "outcome": job.get("outcome", ""),
            "current_workflow_revision_id": str(active["id"]) if active else "",
            "current_workflow_revision_version": active["version"] if active else None,
            "is_workflow_outdated": is_outdated,
            "node_summaries": summaries,
            "completed_nodes": completed_nodes,
            "total_nodes": total_nodes,
            "active_node_key": active_node_key,
            "error_summary": error_summary,
            "execution_control": {
                "mode": job.get("execution_mode", "full"),
                "target_node_key": job.get("target_node_key"),
                "paused": bool(job.get("execution_paused", False)),
                "pause_reason": job.get("pause_reason", ""),
            },
        }

    def list_jobs(
        self,
        workspace_id: str,
        workflow_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.job_db.list_jobs(
            workspace_id=workspace_id,
            workflow_key=workflow_key,
            status=status,
        )
        return summarize_paginated_jobs(self, self.job_db, jobs)

    def detail(self, job_id: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        definition = self._definition_for_job(job)
        nodes = self.job_db.list_job_nodes(job_id)
        nodes_with_definition = job_nodes_with_definition(nodes, definition)
        worker_map = claimed_worker_map(self.job_db.path, job_id)
        agent_map = agent_route_map(
            self.job_db.path, str(job["workspace_id"]), str(job["workflow_key"])
        )
        for node in nodes_with_definition:
            # P-0.5: non-Agent-routed nodes always run on the implicit code
            # pool; the projection is a constant, no configuration lookup.
            is_agent = agent_map.get(node["node_key"]) is not None
            node["executor_id"] = None if is_agent else CODE_EXECUTOR_ID
            node["executor_kind"] = None if is_agent else "code"
            node["worker_id"] = worker_map.get(node["node_key"])
            node["agent_id"] = agent_map.get(node["node_key"])
        return {
            "job": self._job_summary(job, nodes, definition),
            "nodes": nodes_with_definition,
            "runs": [
                resolve_record_paths(run, self.settings.data_dir, _RUN_PATH_FIELDS)
                for run in self.job_db.list_node_runs(job_id)
            ],
            "artifacts": self._artifact_names(job),
        }

    def workspace_runs(
        self,
        workspace_id: str,
        status: str | None = None,
        node_key: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        runs = self.job_db.list_workspace_node_runs(
            workspace_id,
            status=status,
            node_key=node_key,
            job_id=job_id,
            limit=limit,
        )
        return [resolve_record_paths(run, self.settings.data_dir, _RUN_PATH_FIELDS) for run in runs]

    def workspace_dag(self, workspace_id: str) -> dict[str, Any]:
        return build_workspace_dag(self.job_db, workspace_id)
