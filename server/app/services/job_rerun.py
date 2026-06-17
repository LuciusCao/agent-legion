import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager, broadcast_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_staged_cleanup import commit_staged_outputs
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings
from server.app.workflows.scheduler import downstream_nodes

logger = logging.getLogger(__name__)


class JobRerunService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        workflows: WorkflowCatalogService,
        artifact_service: JobArtifactMutationService | None = None,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.workflows = workflows
        self.artifact_service = artifact_service or JobArtifactMutationService(settings.jobs_dir)
        self.clock = clock
        self.job_event_manager = job_event_manager

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

        definition = self.workflows.definition(str(job["workflow_key"]))
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
        staged = None
        try:
            with self.job_db.lease_guarded_mutation(
                job_id,
                self._now(),
                reject_running_nodes=True,
            ) as conn:
                staged = self.artifact_service.stage_outputs(job, [node_key], definition)
                self.job_db.mark_nodes_for_rerun_in_transaction(
                    conn, job_id, [node_key], {node_key: stale_nodes}
                )
        except JobMutationConflict as exc:
            if staged is not None:
                staged.rollback()
            return self._result(job_id, "skipped", node_key, exc.reason_code, str(exc))
        except ValueError as exc:
            if staged is not None:
                staged.rollback()
            return self._result(job_id, "failed", node_key, "cleanup_failed", str(exc))
        except Exception as exc:
            logger.exception("Failed to mark nodes for rerun for job %s", job_id)
            if staged is not None:
                staged.rollback()
            return self._result(
                job_id,
                "failed",
                node_key,
                "rerun_failed",
                str(exc),
            )

        commit_staged_outputs(staged, job_id, "rerun")
        broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "succeeded", node_key)

    def batch_rerun(
        self, workspace_id: str, job_ids: list[str], node_key: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job_id in self._normalize_values(job_ids):
            results.append(self.rerun(workspace_id, job_id, node_key))
        return results

    @staticmethod
    def _normalize_values(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
