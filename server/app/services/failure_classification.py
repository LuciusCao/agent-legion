"""Failure classification write-path helpers.

Rules live in ``_failure_classification_rules``; both executor links (Agent
Worker, local Pi runner) converge on the lease finish path, which classifies
once via ``resolve_failure_fields``. Orphan recovery assigns
``worker_orphaned`` directly at its write path.
"""

from __future__ import annotations

from server.app.executors.models import ExecutionResult
from server.app.services._failure_classification_rules import (
    FAILURE_CATEGORIES,
    TIMEOUT_EXIT_CODE,
    classify_failure,
)

__all__ = [
    "FAILURE_CATEGORIES",
    "TIMEOUT_EXIT_CODE",
    "classify_execution_result",
    "classify_failure",
    "resolve_failure_fields",
]


def resolve_failure_fields(
    status: str,
    exit_code: int | None,
    error_message: str,
    declared_category: str = "",
    declared_detail: str = "",
) -> tuple[str, str]:
    """Failure fields to persist for one finished run.

    Non-failed runs store empty fields; an explicit classification from the
    caller (e.g. orphan recovery) wins over rule-based classification.
    """
    if status != "failed":
        return "", ""
    if declared_category:
        return declared_category, declared_detail
    return classify_failure(exit_code, error_message)


def classify_execution_result(result: ExecutionResult) -> tuple[str, str]:
    """Failure fields for one finished ``ExecutionResult`` (lease finish path)."""
    return resolve_failure_fields(
        result.status,
        result.exit_code,
        result.error_message,
        result.failure_category,
        result.failure_detail,
    )
