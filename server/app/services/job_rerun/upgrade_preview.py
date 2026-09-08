"""Read-only batch-upgrade preview: eligible/total counts for a selection.

The upgrade sibling of ``job_rerun.preview`` (#532 PR-A, PR #541 P2): a
campaign's upgrade preview must judge eligibility the way the upgrade write
path (JobWorkflowUpgradeService.upgrade) does — not-current against the
active revision — instead of reusing the rerun preview's node_key-shaped
judgement, which answers zero for every job of an upgrade selection. Set
based like its sibling: one selection scan, one narrow bulk job query, one
active-revision read, joined in memory. Nothing writes.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import resolve_batch_selection

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


def batch_upgrade_preview(
    service: JobRerunService,
    workspace_id: str,
    job_ids: list[str] | None = None,
    *,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> dict[str, int]:
    """Return {"total_count", "eligible_count"} for the selection; no writes.

    Eligibility mirrors JobWorkflowUpgradeService.upgrade's window: a job is
    eligible when it exists in the workspace and is NOT already current —
    pin AND definition snapshot both equal the active revision's (the pin
    alone is not "current": a stale snapshot must re-pin to heal, so such
    jobs stay eligible). Workspaces without an active revision have every
    job ineligible (upgrade would fail them). Busy (active-lease) jobs stay
    eligible: upgrade skips them per-batch, and a preview is a snapshot,
    not a promise — the same liveness caveat the rerun preview carries.
    """
    ids = list(
        dict.fromkeys(
            value.strip()
            for value in resolve_batch_selection(
                service.job_db, workspace_id, job_ids, job_filter, exclude_ids
            )
            if value.strip()
        )
    )
    if not ids:
        return {"total_count": 0, "eligible_count": 0}

    jobs = service.job_db.list_job_rerun_states_for_jobs(workspace_id, ids)
    active = service.job_db.get_active_workflow_revision(workspace_id, workspace_id)
    if active is None:
        return {"total_count": len(ids), "eligible_count": 0}
    active_revision_id = str(active["id"])
    active_definition_json = str(active["definition_json"])

    eligible = 0
    for job_id in ids:
        job = jobs.get(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            continue
        already_current = (
            str(job.get("workflow_revision_id") or "") == active_revision_id
            and str(job.get("workflow_definition_snapshot_json") or "") == active_definition_json
        )
        if not already_current:
            eligible += 1
    return {"total_count": len(ids), "eligible_count": eligible}
