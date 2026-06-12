import glob
import logging
import shutil
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.pipelines.artifacts import clear_rerun_outputs
from server.app.pipelines.scheduler import downstream_nodes
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings

logger = logging.getLogger(__name__)


class JobRerunService:
    def __init__(self, job_db: JobQueries, settings: Settings, pipelines: PipelineCatalogService):
        self.job_db = job_db
        self.settings = settings
        self.pipelines = pipelines

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _job_has_running_nodes(self, job_id: str) -> bool:
        return any(node["status"] == "running" for node in self.job_db.list_job_nodes(job_id))

    def rerun_node(self, job_id: str, node_key: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        definition = self.pipelines.definition(str(job["pipeline_key"]))
        if node_key not in definition.nodes:
            raise NotFoundError("Node not found")
        if self._job_has_running_nodes(job_id):
            raise InvalidOperationError("Cannot rerun a running job")
        stale_nodes = downstream_nodes(definition, node_key)
        try:
            clear_rerun_outputs(definition, node_key, Path(str(job["storage_dir"])))
        except ValueError as exc:
            raise InvalidOperationError(f"Cleanup failed: {exc}") from exc
        try:
            self.job_db.mark_node_for_rerun(job_id, node_key, stale_nodes)
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc
        return {"job_id": job_id, "node_key": node_key, "stale_nodes": stale_nodes}

    def batch_rerun(self, workspace_id: str, job_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job_id in self._normalize_values(job_ids):
            job = self.job_db.get_job(job_id)
            if job is None or job["workspace_id"] != workspace_id:
                results.append({"job_id": job_id, "status": "not_found"})
                continue
            if self._job_has_running_nodes(job_id):
                results.append({"job_id": job_id, "status": "skipped", "reason": "running"})
                continue
            definition = self.pipelines.definition(str(job["pipeline_key"]))
            root_nodes = [key for key, node in definition.nodes.items() if not node.after]
            if not root_nodes:
                results.append({"job_id": job_id, "status": "skipped", "reason": "no_root_node"})
                continue
            first_node = root_nodes[0]
            stale_nodes = downstream_nodes(definition, first_node)
            try:
                clear_rerun_outputs(definition, first_node, Path(str(job["storage_dir"])))
            except ValueError as exc:
                results.append(
                    {
                        "job_id": job_id,
                        "status": "skipped",
                        "reason": f"cleanup_failed: {exc}",
                    }
                )
                continue
            self.job_db.mark_node_for_rerun(job_id, first_node, stale_nodes)
            results.append({"job_id": job_id, "status": "rerun", "node_key": first_node})
        return results

    def delete(self, job_id: str) -> str:
        job = self._job_or_404(job_id)
        if self._job_has_running_nodes(job_id):
            raise InvalidOperationError("Cannot delete a running job")
        storage_dir = Path(str(job["storage_dir"]))
        try:
            self.job_db.delete_job(job_id)
        except ValueError as exc:
            raise InvalidOperationError(str(exc)) from exc
        if storage_dir.exists() and storage_dir.is_dir():
            shutil.rmtree(storage_dir)
        for log_path in glob.glob(str(self.settings.logs_dir / "jobs" / f"{job_id}-*.log")):
            Path(log_path).unlink(missing_ok=True)
        return job_id

    def batch_delete(self, workspace_id: str, job_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job_id in self._normalize_values(job_ids):
            job = self.job_db.get_job(job_id)
            if job is None or job["workspace_id"] != workspace_id:
                results.append({"job_id": job_id, "status": "not_found"})
                continue
            if self._job_has_running_nodes(job_id):
                results.append({"job_id": job_id, "status": "skipped", "reason": "running"})
                continue
            storage_dir = Path(str(job["storage_dir"]))
            self.job_db.delete_job(job_id)
            if storage_dir.exists() and storage_dir.is_dir():
                shutil.rmtree(storage_dir)
            for log_path in glob.glob(str(self.settings.logs_dir / "jobs" / f"{job_id}-*.log")):
                Path(log_path).unlink(missing_ok=True)
            results.append({"job_id": job_id, "status": "deleted"})
        return results

    @staticmethod
    def _normalize_values(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
