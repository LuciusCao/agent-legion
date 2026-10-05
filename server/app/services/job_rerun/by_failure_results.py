"""Per-job result assembly for rerun-by-failure-category batches."""

from __future__ import annotations

from typing import Any

from server.app.services.job_operation_error import JobOperationResult


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


def assemble_rerun_targets(job_id: str, node_results: list[JobOperationResult]) -> dict[str, Any]:
    """Fold per-node rerun results into the per-job category result."""
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
