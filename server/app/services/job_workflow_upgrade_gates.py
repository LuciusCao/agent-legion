"""upgrade-workflow 的前置校验门（issue #645 review 拆分）。

只读校验链（not_found / wrong_workspace / no_active_revision /
already_current / busy / invalid_node_config）与执行输入的组装
（definition、re-freeze 的 frozen_config_json）。任何一步不满足都返回
一个「短路结果」；全部通过返回组装好的 :class:`UpgradeContext`，由
``job_workflow_upgrade.JobWorkflowUpgradeService`` 统一应用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_result import upgrade_result
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.jobs import JobQueries


@dataclass(frozen=True)
class UpgradeContext:
    """校验通过后的升级执行输入（全部字段已归一为 str）。"""

    job: dict[str, Any]
    active: dict[str, Any]
    definition: WorkflowDefinition
    frozen_config_json: str | None
    now: datetime


def resolve_upgrade_context(
    job_db: JobQueries,
    lease_repo: ExecutorLeaseRepository,
    workspace_id: str,
    job_id: str,
    *,
    mode: str,
) -> UpgradeContext | dict[str, Any]:
    """校验并组装升级输入；不满足时返回短路结果 dict（status != None 区分）。

    调用方约定：返回 ``UpgradeContext`` 即进入统一应用（事务 + 暂存），
    返回 dict 即直接作为该 job 的升级结果（skipped/failed）。
    """
    job = job_db.get_job(job_id)
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

    active = job_db.get_active_workflow_revision(str(job["workspace_id"]), str(job["workspace_id"]))
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
    if lease_repo.has_active_for_job(job_id, now):
        return upgrade_result(
            job_id, "skipped", "busy", "Job has an active executor lease", mode=mode
        )

    definition = workflow_definition_from_dict(json.loads(active["definition_json"]))
    try:
        # Re-freeze node config as intake would on the active revision, so
        # node-level config fixes reach old jobs via upgrade instead of
        # forcing a re-intake. Validated fully before any mutation below.
        frozen_config_json = intake_frozen_config_json(job_db, workspace_id, definition)
    except ValueError as exc:
        return upgrade_result(job_id, "failed", "invalid_node_config", str(exc), mode=mode)
    return UpgradeContext(
        job=job,
        active=active,
        definition=definition,
        frozen_config_json=frozen_config_json,
        now=now,
    )
