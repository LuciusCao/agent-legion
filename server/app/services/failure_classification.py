"""Classify failed node runs into a coarse category plus a fine-grained detail.

The same technical failure reaches ``node_runs.error_message`` in two textual
shapes: Agent Workers report the raw message (e.g. ``terminated``) while the
local Pi runner wraps model errors as ``Pi model call failed: ...``. Both
shapes converge on the lease finish path, which classifies once via
``resolve_failure_fields``. Orphan recovery is the single exception: it
assigns ``(technical, worker_orphaned)`` directly at its write path.

Core principle: anything unrecognized classifies as ``unknown`` — never
default to ``technical``.
"""

from __future__ import annotations

import re

from server.app.executors.models import ExecutionResult

CATEGORY_TECHNICAL = "technical"
CATEGORY_BUSINESS = "business"
CATEGORY_UNKNOWN = "unknown"
FAILURE_CATEGORIES = (CATEGORY_TECHNICAL, CATEGORY_BUSINESS, CATEGORY_UNKNOWN)

_REVIEW_REJECTED_PREFIX = "review_rejected:"
_PI_MODEL_CALL_PREFIX = "Pi model call failed:"
_MISSING_OUTPUTS_PREFIX = "Missing outputs:"
_NO_OUTPUT_ARTIFACTS_PREFIX = "Agent Worker did not report output artifacts"
_UNPACK_FAILURE = "failed to unpack Agent result"
_PROCESS_EXITED_RE = re.compile(r"^Agent process exited (\d+)$")
_TERMINATED_WORD_RE = re.compile(r"\bterminated\b")

TIMEOUT_EXIT_CODE = 124


def classify_failure(exit_code: int | None, error_message: str) -> tuple[str, str]:
    """Map one failed run's exit code and message to (category, detail)."""
    message = error_message or ""

    if message.startswith(_REVIEW_REJECTED_PREFIX):
        return CATEGORY_BUSINESS, "review_rejected"

    exited = _PROCESS_EXITED_RE.match(message)
    if exit_code == TIMEOUT_EXIT_CODE or (exited is not None and exited.group(1) == "124"):
        return CATEGORY_TECHNICAL, "timeout"

    # Provider stream interruptions, raw (Worker-reported) or Pi-runner-wrapped.
    if _TERMINATED_WORD_RE.search(message) or "Connection error" in message:
        return CATEGORY_TECHNICAL, "provider_stream"

    if message.startswith(_PI_MODEL_CALL_PREFIX):
        return CATEGORY_TECHNICAL, "provider_request"

    if "CmsClientError" in message or "CMS token" in message:
        return CATEGORY_TECHNICAL, "cms_auth"

    if "[Errno 24]" in message or "Too many open files" in message:
        return CATEGORY_TECHNICAL, "resource_limit"

    if message.startswith((_MISSING_OUTPUTS_PREFIX, _NO_OUTPUT_ARTIFACTS_PREFIX)):
        return CATEGORY_UNKNOWN, "output_missing"

    if _UNPACK_FAILURE in message:
        return CATEGORY_TECHNICAL, "execution_error"

    if exited is not None:
        return CATEGORY_UNKNOWN, "execution_error"

    return CATEGORY_UNKNOWN, "unknown"


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
