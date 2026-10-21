"""Per-kind chunk execution for the batch-ops feeder (#946).

Every kind executes through the SAME batch entry point the synchronous path
uses (per-job result dicts identical by construction); the feeder only adds
chunking, the replay guard, and the cursor. ``package`` is the exception: it
is batch-level by nature (one zip per operation), so the chunks record
per-job eligibility and the completion hook runs the packaging phase once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import KIND_DELETE
from server.app.services import job_workflow_upgrade_batch
from server.app.services.job_rerun import batch_ops as rerun_batch_ops


@dataclass
class BatchOpsExecutors:
    """The per-kind services (the JobServices bundle members + packages)."""

    rerun: Any  # JobRerunService
    deletion: Any  # JobDeletionService
    execution: Any  # JobExecutionService
    pause: Any  # JobPauseService
    upgrade: Any  # JobWorkflowUpgradeService
    packages: Any  # JobPackageService


def run_chunk(
    executors: BatchOpsExecutors,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute one chunk; results align with ``ids`` (see feeder._map_results)."""
    if kind == "rerun":
        return [
            dict(result)
            for result in executors.rerun.batch_rerun(
                workspace_id,
                ids,
                params.get("node_key"),
                from_failed_node=bool(params.get("from_failed_node")),
            )
        ]
    if kind == "rerun_by_failure":
        return [
            dict(result)
            for result in executors.rerun.rerun_by_failure_category(
                workspace_id,
                str(params["category"]),
                strategy=str(params.get("strategy") or "auto"),
                job_ids=ids,
                from_node_key=params.get("from_node_key"),
            )
        ]
    if kind == KIND_DELETE:
        return [
            dict(result)
            for result in rerun_batch_ops.batch_delete(executors.deletion, workspace_id, ids)
        ]
    if kind == "run_to":
        return [
            dict(result)
            for result in rerun_batch_ops.batch_run_to(
                executors.execution,
                workspace_id,
                ids,
                str(params["target_node_key"]),
                params.get("start_node_key"),
            )
        ]
    if kind == "pause":
        return [
            dict(result)
            for result in executors.pause.batch_pause(
                workspace_id,
                ids,
                params.get("reason"),
                operator=str(params.get("operator") or ""),
            )
        ]
    if kind == "resume":
        return [dict(result) for result in executors.pause.batch_resume(workspace_id, ids)]
    if kind == "upgrade_workflow":
        return [
            dict(result)
            for result in job_workflow_upgrade_batch.batch_upgrade(
                executors.upgrade, workspace_id, ids, mode=str(params.get("mode") or "clean")
            )
        ]
    if kind == "package":
        # Chunks record eligibility only; the completion hook packages once.
        results, _eligible = executors.packages.check_package_eligibility(workspace_id, ids)
        return [dict(result) for result in results]
    if kind == "clear_packed":
        return [
            dict(result)
            for result in executors.packages.clear_packed_status(workspace_id, job_ids=ids)
        ]
    raise ValueError(f"unknown batch operation kind: {kind!r}")


def run_completion(
    executors: BatchOpsExecutors,
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    op_id: str,
) -> dict[str, Any] | None:
    """Terminal phase for kinds with batch-level output (package only).

    Re-validates eligibility at completion time (a job packaged-eligible when
    its chunk ran may have been deleted or re-run since); the per-job results
    keep the chunk-time verdicts, the package contains what still qualifies.
    """
    if kind != "package":
        return None
    eligible_ids = job_db.list_job_batch_operation_succeeded_job_ids(op_id)
    if not eligible_ids:
        return {}
    jobs_by_id = {
        str(job["id"]): job for job in job_db.list_jobs_by_ids(workspace_id, eligible_ids)
    }
    eligible_jobs = [
        jobs_by_id[job_id]
        for job_id in eligible_ids
        if job_id in jobs_by_id and jobs_by_id[job_id].get("status") == "completed"
    ]
    output: dict[str, Any] = executors.packages.package_eligible_jobs(workspace_id, eligible_jobs)
    return output
