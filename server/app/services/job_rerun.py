import glob
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.pipelines.artifacts import clear_rerun_outputs
from server.app.pipelines.scheduler import downstream_nodes
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.settings import Settings

logger = logging.getLogger(__name__)


class JobRerunService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        pipelines: PipelineCatalogService,
        artifact_service: JobArtifactMutationService | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.pipelines = pipelines
        self.artifact_service = artifact_service or JobArtifactMutationService()
        self.clock = clock

    def _now(self) -> datetime:
        if self.clock is not None:
            return datetime.fromtimestamp(self.clock(), tz=UTC)
        return datetime.now(UTC)

    def _result(
        self,
        job_id: str,
        status: str,
        node_key: str | None = None,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "operation": "rerun",
            "status": status,
            "node_key": node_key,
            "reason_code": reason_code,
            "message": message,
        }

    def _job_has_running_nodes(self, job_id: str) -> bool:
        return any(node["status"] == "running" for node in self.job_db.list_job_nodes(job_id))

    def rerun(self, workspace_id: str, job_id: str, node_key: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            return self._result(job_id, "failed", node_key, "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            return self._result(
                job_id,
                "failed",
                node_key,
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )

        definition = self.pipelines.definition(str(job["pipeline_key"]))
        if node_key not in definition.nodes:
            return self._result(
                job_id,
                "failed",
                node_key,
                "node_not_found",
                f"Node {node_key} not found in pipeline",
            )

        if self.job_db.get_job_node(job_id, node_key) is None:
            return self._result(
                job_id,
                "failed",
                node_key,
                "node_not_found",
                f"Node {node_key} not found for job",
            )

        if self.lease_repo.has_active_for_node(job_id, node_key, self._now()):
            return self._result(
                job_id,
                "skipped",
                node_key,
                "busy",
                "Node has an active executor lease",
            )

        if self._job_has_running_nodes(job_id):
            return self._result(
                job_id,
                "skipped",
                node_key,
                "busy",
                "Job has running nodes",
            )

        stale_nodes = downstream_nodes(definition, node_key)
        try:
            staged = self.artifact_service.stage_outputs(job, [node_key], definition)
        except ValueError as exc:
            return self._result(
                job_id,
                "failed",
                node_key,
                "cleanup_failed",
                str(exc),
            )

        try:
            self.job_db.mark_nodes_for_rerun_atomic(job_id, [node_key], {node_key: stale_nodes})
        except Exception as exc:
            logger.exception("Failed to mark nodes for rerun for job %s", job_id)
            staged.rollback()
            return self._result(
                job_id,
                "failed",
                node_key,
                "rerun_failed",
                str(exc),
            )

        staged.commit()
        return self._result(job_id, "succeeded", node_key)

    def batch_rerun(
        self, workspace_id: str, job_ids: list[str], node_key: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job_id in self._normalize_values(job_ids):
            results.append(self.rerun(workspace_id, job_id, node_key))
        return results

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

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    @staticmethod
    def _normalize_values(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
