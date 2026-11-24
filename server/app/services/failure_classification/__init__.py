"""Failure classification write-path helpers.

Rules live in ``failure_classification.rules`` (message markers in
``failure_classification.markers``); both executor links (Agent Worker, local
Pi runner) converge on the lease finish path, which classifies once via
``resolve_failure_fields``. Orphan recovery assigns ``worker_orphaned``
directly at its write path.
"""

from __future__ import annotations

from server.app.executors.models import ExecutionResult
from server.app.services.failure_classification.rules import (
    CATEGORY_UNKNOWN,
    FAILURE_CATEGORIES,
    TIMEOUT_EXIT_CODE,
    TRANSIENT_RETRY_DETAILS,
    classify_failure,
)
from server.app.workflows.revision_format import definition_from_job_snapshot

__all__ = [
    "FAILURE_CATEGORIES",
    "TIMEOUT_EXIT_CODE",
    "classify_execution_result",
    "classify_failure",
    "is_transient_retryable",
    "node_failure_default",
    "resolve_failure_fields",
]


def is_transient_retryable(failure_detail: str) -> bool:
    """Transient details retry at the lease finish path instead of failing the job."""
    return failure_detail in TRANSIENT_RETRY_DETAILS


def node_failure_default(snapshot_json: str, node_key: str) -> str:
    definition = definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot_json})
    if definition is None or node_key not in definition.nodes:
        return ""
    return definition.nodes[node_key].failure_default


def resolve_failure_fields(
    status: str,
    exit_code: int | None,
    error_message: str,
    declared_category: str = "",
    declared_detail: str = "",
    failure_default: str = "",
) -> tuple[str, str]:
    """Resolve declared facts, known rules, then the node's unknown-only default."""
    if status != "failed":
        return "", ""
    if declared_category:
        return declared_category, declared_detail
    category, detail = classify_failure(exit_code, error_message)
    if category == CATEGORY_UNKNOWN and failure_default in FAILURE_CATEGORIES:
        return failure_default, detail
    return category, detail


def classify_execution_result(
    result: ExecutionResult, *, failure_default: str = ""
) -> tuple[str, str]:
    """Failure fields for one finished ``ExecutionResult`` (lease finish path)."""
    return resolve_failure_fields(
        result.status,
        result.exit_code,
        result.error_message,
        result.failure_category,
        result.failure_detail,
        failure_default,
    )
