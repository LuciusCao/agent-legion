from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_staging import execute_staged_upgrade
from server.app.workflows.definition import workflow_definition_from_dict


class JobWorkflowUpgradeService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        artifact_service: JobArtifactMutationService | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.artifact_service = artifact_service or JobArtifactMutationService(
            getattr(job_db, "jobs_dir", None)
        )
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # #508 同款：清单行 GC 需要提交后的对象删除；None = 无对象存储。
        self.object_store = object_store

    def _result(
        self,
        job_id: str,
        status: str,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "operation": "upgrade_workflow",
            "status": status,
            "node_key": None,
            "reason_code": reason_code,
            "message": message,
        }

    def upgrade(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            return self._result(job_id, "failed", "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            return self._result(
                job_id,
                "failed",
                "not_found",
                "Job not found",
            )

        active = self.job_db.get_active_workflow_revision(
            str(job["workspace_id"]), str(job["workspace_id"])
        )
        if active is None:
            return self._result(
                job_id,
                "failed",
                "no_active_revision",
                "Workspace has no active workflow revision",
            )
        # Skip only when the job truly matches the active revision. A job can
        # pin the active revision id yet carry a stale definition snapshot
        # (older upgrade paths moved the pin without swapping the snapshot);
        # dispatch resolves node execution/config from that snapshot, so such
        # jobs must be re-pinned to heal instead of being skipped forever.
        # The actual snapshot content is compared, not the independently
        # stored hash column — the two have no consistency constraint.
        if str(job.get("workflow_revision_id") or "") == str(active["id"]) and str(
            job.get("workflow_definition_snapshot_json") or ""
        ) == str(active["definition_json"]):
            return self._result(job_id, "skipped", "already_current", "Job is already current")

        now = datetime.now(UTC)
        if self.lease_repo.has_active_for_job(job_id, now):
            return self._result(job_id, "skipped", "busy", "Job has an active executor lease")

        definition = workflow_definition_from_dict(json.loads(active["definition_json"]))
        try:
            # Re-freeze node config as intake would on the active revision, so
            # node-level config fixes reach old jobs via upgrade instead of
            # forcing a re-intake. Validated fully before any mutation below.
            frozen_config_json = intake_frozen_config_json(self.job_db, workspace_id, definition)
        except ValueError as exc:
            return self._result(job_id, "failed", "invalid_node_config", str(exc))
        try:
            # The intake batch's node_code_versions deliberately stay frozen:
            # the batch payload is shared by every job in the batch. Since
            # #115 ordinary jobs dispatch the latest published code anyway;
            # the frozen pins only matter to quality-replay batches.
            execute_staged_upgrade(self, job, job_id, active, definition, frozen_config_json, now)
        except JobMutationConflict as exc:
            return self._result(job_id, "skipped", exc.reason_code, str(exc))

        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id)
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "succeeded")
