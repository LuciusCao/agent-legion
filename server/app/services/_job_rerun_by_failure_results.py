"""Per-job result assembly for rerun-by-failure-category batches."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services._job_rerun_single import execute_rerun_result

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


def job_failure_result(
    job_id: str,
    status: str,
    reason_code: str | None,
    message: str | None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": "rerun",
        "status": status,
        "node_key": None,
        "reason_code": reason_code,
        "message": message,
        "rerun_nodes": [],
    }


def execute_rerun_targets(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    targets: list[str],
) -> dict[str, Any]:
    node_results = [execute_rerun_result(service, job, job_id, target) for target in targets]
    rerun_nodes = [str(r["node_key"]) for r in node_results if r["status"] == "succeeded"]
    failures = [r for r in node_results if r["status"] == "failed"]
    skips = [r for r in node_results if r["status"] == "skipped"]
    result = job_failure_result(job_id, "succeeded", None, None)
    if failures:
        result["status"] = "failed"
        result["reason_code"] = failures[0]["reason_code"]
        result["message"] = failures[0]["message"]
    elif skips and not rerun_nodes:
        result["status"] = "skipped"
        result["reason_code"] = skips[0]["reason_code"]
        result["message"] = skips[0]["message"]
    result["rerun_nodes"] = rerun_nodes
    return result
