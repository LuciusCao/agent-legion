from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_node_executor_resolver import resolve_node_executors
from server.app.services.job_node_ordering import ordered_job_nodes
from server.app.services.job_path_projection import resolve_record_paths
from server.app.services.job_query_presenters import (
    artifact_names,
    job_nodes_with_definition,
    node_summary,
)
from server.app.services.job_workflow_versions import is_workflow_outdated
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.services.workspace_dag import build_workspace_dag
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
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

    def _job_summary(
        self,
        job: dict[str, Any],
        nodes: list[dict[str, Any]],
        definition: WorkflowDefinition,
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

        active = self.job_db.get_active_workflow_revision(
            str(job["workspace_id"]), str(job["workflow_key"])
        )
        job_workflow_version = job.get("workflow_version")
        is_outdated = is_workflow_outdated(job, active)

        job = resolve_record_paths(job, self.settings.data_dir, {"storage_dir"})
        return {
            **job,
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
        job_ids = [str(job["id"]) for job in jobs]
        nodes_by_job = self.job_db.list_job_nodes_for_jobs(job_ids)

        definitions: dict[tuple[str, str], WorkflowDefinition] = {}

        def _definition(job: dict[str, Any]) -> WorkflowDefinition:
            key = (str(job["workflow_key"]), str(job.get("workflow_definition_hash") or ""))
            if key not in definitions:
                definitions[key] = self._definition_for_job(job)
            return definitions[key]

        return [
            self._job_summary(job, nodes_by_job.get(str(job["id"]), []), _definition(job))
            for job in jobs
        ]

    def detail(self, job_id: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        definition = self._definition_for_job(job)
        nodes = self.job_db.list_job_nodes(job_id)
        nodes_with_definition = job_nodes_with_definition(nodes, definition)
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
            "artifacts": artifact_names(job, self.settings),
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
