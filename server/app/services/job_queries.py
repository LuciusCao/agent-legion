from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings


class JobQueryService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        pipelines: PipelineCatalogService,
    ):
        self.job_db = job_db
        self.settings = settings
        self.pipelines = pipelines

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _job_nodes_with_definition(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        definition = self.pipelines.definition(str(job["pipeline_key"]))
        nodes = self.job_db.list_job_nodes(str(job["id"]))
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

    def _artifact_names(self, job: dict[str, Any]) -> list[str]:
        base = Path(str(job["storage_dir"]))
        if not base.exists():
            return []
        return sorted(path.name for path in base.iterdir() if path.is_file())

    def list_jobs(
        self,
        workspace_id: str,
        pipeline_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.job_db.list_jobs(
            workspace_id=workspace_id,
            pipeline_key=pipeline_key,
            status=status,
        )

    def detail(self, job_id: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        return {
            "job": job,
            "nodes": self._job_nodes_with_definition(job),
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
        definition = self.pipelines.definition(pipeline_key)
        counts = self.job_db.count_workspace_job_nodes_by_status(workspace_id, pipeline_key)
        statuses = ["pending", "running", "completed", "failed", "stale"]
        return {
            "pipeline": {
                "key": definition.key,
                "label": definition.label,
                "concurrency": {
                    "local": definition.concurrency.local,
                    "agent": definition.concurrency.agent,
                },
            },
            "nodes": [
                {
                    "key": node.key,
                    "label": node.label,
                    "capability": node.capability,
                    "runner": node.runner,
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
