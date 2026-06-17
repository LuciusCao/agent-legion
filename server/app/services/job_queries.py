from typing import Any

from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.services.job_errors import NotFoundError
from server.app.services.job_node_executor_resolver import resolve_node_executors
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


class JobQueryService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        pipelines: PipelineCatalogService,
        workspace_executor_config: WorkspaceExecutorConfigurationService,
    ):
        self.job_db = job_db
        self.settings = settings
        self.pipelines = pipelines
        self.workspace_executor_config = workspace_executor_config

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _definition(self, pipeline_key: str) -> PipelineDefinition:
        return self.pipelines.definition(pipeline_key)

    def _job_nodes_with_definition(
        self,
        job: dict[str, Any],
        nodes: list[dict[str, Any]],
        definition: PipelineDefinition,
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
        definition: PipelineDefinition,
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
        definition: PipelineDefinition,
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

        control = self.job_db.get_job_execution_control(job["id"])
        return {
            **job,
            "node_summaries": summaries,
            "completed_nodes": completed_nodes,
            "total_nodes": total_nodes,
            "active_node_key": active_node_key,
            "error_summary": error_summary,
            "execution_control": {
                "mode": control["execution_mode"] if control else "full",
                "target_node_key": control["target_node_key"] if control else None,
                "paused": control["execution_paused"] if control else False,
                "pause_reason": control["pause_reason"] if control else "",
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
        pipeline_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.job_db.list_jobs(
            workspace_id=workspace_id,
            pipeline_key=pipeline_key,
            status=status,
        )
        job_ids = [str(job["id"]) for job in jobs]
        nodes_by_job = self.job_db.list_job_nodes_for_jobs(job_ids)

        definitions: dict[str, PipelineDefinition] = {}
        for job in jobs:
            key = str(job["pipeline_key"])
            if key not in definitions:
                definitions[key] = self._definition(key)

        return [
            self._job_summary(
                job, nodes_by_job.get(str(job["id"]), []), definitions[str(job["pipeline_key"])]
            )
            for job in jobs
        ]

    def detail(self, job_id: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        definition = self._definition(str(job["pipeline_key"]))
        nodes = self.job_db.list_job_nodes(job_id)
        nodes_with_definition = self._job_nodes_with_definition(job, nodes, definition)
        executor_map = resolve_node_executors(
            str(job["workspace_id"]),
            str(job["pipeline_key"]),
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
            "runs": self.job_db.list_node_runs(job_id),
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
        return self.job_db.list_workspace_node_runs(
            workspace_id,
            status=status,
            node_key=node_key,
            job_id=job_id,
            limit=limit,
        )

    def workspace_dag(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        pipeline_key = str(workspace.get("default_pipeline_key") or "question_content")
        definition = self._definition(pipeline_key)
        counts = self.job_db.count_workspace_job_nodes_by_status(workspace_id, pipeline_key)
        statuses = ["pending", "running", "completed", "failed", "stale"]
        return {
            "pipeline": {
                "key": definition.key,
                "label": definition.label,
            },
            "nodes": [
                {
                    "key": node.key,
                    "label": node.label,
                    "capability": node.capability,
                    "after": node.after,
                    "inputs": node.inputs,
                    "outputs": node.outputs,
                    "status_counts": {
                        status: counts.get(node.key, {}).get(status, 0) for status in statuses
                    },
                }
                for node in definition.nodes.values()
            ],
        }
