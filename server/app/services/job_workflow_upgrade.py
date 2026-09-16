from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_plan import plan_inherit_nodes
from server.app.services.job_workflow_upgrade_result import upgrade_result
from server.app.workflows.definition import workflow_definition_from_dict

UPGRADE_MODES = ("clean", "inherit")


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

    def upgrade(self, workspace_id: str, job_id: str, *, mode: str = "clean") -> dict[str, Any]:
        if mode not in UPGRADE_MODES:
            raise ValueError(f"Unknown upgrade mode: {mode!r}")
        job = self.job_db.get_job(job_id)
        if job is None:
            return upgrade_result(job_id, "failed", "not_found", "Job not found", mode=mode)
        if job["workspace_id"] != workspace_id:
            return upgrade_result(
                job_id,
                "failed",
                "not_found",
                "Job not found",
                mode=mode,
            )

        active = self.job_db.get_active_workflow_revision(
            str(job["workspace_id"]), str(job["workspace_id"])
        )
        if active is None:
            return upgrade_result(
                job_id,
                "failed",
                "no_active_revision",
                "Workspace has no active workflow revision",
                mode=mode,
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
            return upgrade_result(
                job_id, "skipped", "already_current", "Job is already current", mode=mode
            )

        now = datetime.now(UTC)
        if self.lease_repo.has_active_for_job(job_id, now):
            return upgrade_result(
                job_id, "skipped", "busy", "Job has an active executor lease", mode=mode
            )

        definition = workflow_definition_from_dict(json.loads(active["definition_json"]))
        try:
            # Re-freeze node config as intake would on the active revision, so
            # node-level config fixes reach old jobs via upgrade instead of
            # forcing a re-intake. Validated fully before any mutation below.
            frozen_config_json = intake_frozen_config_json(self.job_db, workspace_id, definition)
        except ValueError as exc:
            return upgrade_result(job_id, "failed", "invalid_node_config", str(exc), mode=mode)

        # inherit 模式的继承集在事务外规划（读路径，纯函数见
        # job_workflow_upgrade_plan）；校验备妥后才进入统一应用。
        inherit_nodes: frozenset[str] = frozenset()
        if mode == "inherit":
            inherit_nodes = plan_inherit_nodes(
                self.job_db, job, workspace_id, definition, frozen_config_json
            )
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
                stats = upgrade_job_workflow_inherit(
                    conn,
                    job_id,
                    workflow_revision_id=str(active["id"]),
                    workflow_version=int(active["version"]),
                    workflow_definition_hash=str(active["definition_hash"]),
                    workflow_definition_snapshot_json=str(active["definition_json"]),
                    node_keys=list(definition.executable_nodes),
                    frozen_config_json=frozen_config_json,
                    inherit_nodes=inherit_nodes,
                )
        except JobMutationConflict as exc:
            return upgrade_result(job_id, "skipped", exc.reason_code, str(exc), mode=mode)

        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id)
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return upgrade_result(
            job_id,
            "succeeded",
            mode=mode,
            kept=stats["kept"],
            rerun=stats["rerun"],
        )
