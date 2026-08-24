from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.workflow_upgrade_mutation import upgrade_job_workflow
from server.app.workflows.definition import workflow_definition_from_dict


class JobWorkflowUpgradeService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

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
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )

        active = self.job_db.get_active_workflow_revision(
            str(job["workspace_id"]), str(job["workflow_key"])
        )
        if active is None:
            return self._result(
                job_id,
                "failed",
                "no_active_revision",
                "Workspace has no active workflow revision",
            )
        if str(job.get("workflow_revision_id") or "") == str(active["id"]):
            return self._result(job_id, "skipped", "already_current", "Job is already current")

        now = datetime.now(UTC)
        if self.lease_repo.has_active_for_job(job_id, now):
            return self._result(job_id, "skipped", "busy", "Job has an active executor lease")

        definition = workflow_definition_from_dict(json.loads(active["definition_json"]))
        try:
            # The intake batch's node_code_versions deliberately stay frozen:
            # the batch payload is shared by every job in the batch. Since
            # #115 ordinary jobs dispatch the latest published code anyway;
            # the frozen pins only matter to quality-replay batches.
            with self.job_db.lease_guarded_mutation(
                job_id,
                now,
                reject_running_nodes=True,
            ) as conn:
                upgrade_job_workflow(
                    conn,
                    job_id,
                    workflow_revision_id=str(active["id"]),
                    workflow_version=int(active["version"]),
                    workflow_definition_hash=str(active["definition_hash"]),
                    workflow_definition_snapshot_json=str(active["definition_json"]),
                    node_keys=list(definition.executable_nodes),
                )
        except JobMutationConflict as exc:
            return self._result(job_id, "skipped", exc.reason_code, str(exc))

        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id)
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "succeeded")
