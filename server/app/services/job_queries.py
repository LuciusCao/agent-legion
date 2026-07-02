from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_node_executor_resolver import resolve_node_executors
from server.app.services.job_path_projection import resolve_record_paths
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import definition_from_job_snapshot
from server.app.services.workspace_dag import build_workspace_dag
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition

_RUN_PATH_FIELDS = {"log_path", "run_dir", "session_dir"}


class JobQueryService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workflows: WorkflowCatalogService,
        workspace_executor_config: WorkspaceExecutorConfigurationService,
    ):
        self.job_db = job_db
        self.settings = settings
        self.workflows = workflows
        self.workspace_executor_config = workspace_executor_config

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _definition_for_job(self, job: dict[str, Any]) -> WorkflowDefinition:
        return definition_from_job_snapshot(job) or self.workflows.definition(
            str(job["workflow_key"])
        )

    def _job_nodes_with_definition(
        self,
        job: dict[str, Any],
        nodes: list[dict[str, Any]],
        definition: WorkflowDefinition,
    ) -> list[dict[str, Any]]:
        return [
            {
                **node,
                "label": definition.nodes[node["node_key"]].label
                if node["node_key"] in definition.nodes
                else node["node_key"],
                "capability": definition.nodes[node["node_key"]].capability
                if node["node_key"] in definition.nodes
                else node["node_key"],
                "after": definition.nodes[node["node_key"]].after
                if node["node_key"] in definition.nodes
                else [],
                "inputs": definition.nodes[node["node_key"]].inputs
                if node["node_key"] in definition.nodes
                else [],
                "outputs": definition.nodes[node["node_key"]].outputs
                if node["node_key"] in definition.nodes
                else [],
            }
            for node in nodes
        ]

    def _node_summary(
        self,
        node: dict[str, Any],
        definition: WorkflowDefinition,
    ) -> dict[str, Any]:
        node_key = str(node["node_key"])
        label = definition.nodes[node_key].label if node_key in definition.nodes else node_key
        return {
            "node_key": node_key,
            "label": label,
            "status": str(node["status"]),
            "error_message": str(node.get("error_message", "")),
        }

    def _job_summary(
        self,
        job: dict[str, Any],
        nodes: list[dict[str, Any]],
        definition: WorkflowDefinition,
    ) -> dict[str, Any]:
        summaries = [self._node_summary(node, definition) for node in nodes]
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

        job = resolve_record_paths(job, self.settings.data_dir, {"storage_dir"})
        return {
            **job,
            "workflow_revision_id": job.get("workflow_revision_id", ""),
            "workflow_definition_hash": job.get("workflow_definition_hash", ""),
            "outcome": job.get("outcome", ""),
            "current_workflow_revision_id": str(active["id"])
            if (
                active := self.job_db.get_active_workflow_revision(
                    str(job["workspace_id"]), str(job["workflow_key"])
                )
            )
            else "",
            "current_workflow_revision_version": active["version"] if active else None,
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

    def _artifact_names(self, job: dict[str, Any]) -> list[str]:
        base = resolve_job_dir(job, self.settings.jobs_dir)
        if not base.exists():
            return []
        return sorted(path.name for path in base.iterdir() if path.is_file())

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
        job_ids = [str(job["id"]) for job in jobs]
        nodes_by_job = self.job_db.list_job_nodes_for_jobs(job_ids)

        return [
            self._job_summary(
                job, nodes_by_job.get(str(job["id"]), []), self._definition_for_job(job)
            )
            for job in jobs
        ]

    def detail(self, job_id: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        definition = self._definition_for_job(job)
        nodes = self.job_db.list_job_nodes(job_id)
        nodes_with_definition = self._job_nodes_with_definition(job, nodes, definition)
        executor_map = resolve_node_executors(
            str(job["workspace_id"]),
            str(job["workflow_key"]),
            self.workspace_executor_config,
            self.settings,
        )
        for node in nodes_with_definition:
            executor_id, executor_kind = executor_map.get(node["node_key"], (None, None))
            node["executor_id"] = executor_id
            node["executor_kind"] = executor_kind
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
