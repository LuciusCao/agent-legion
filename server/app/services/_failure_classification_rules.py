"""Rule table mapping a failed run's exit code and message to (category, detail).

The same technical failure reaches ``node_runs.error_message`` in two textual
shapes: Agent Workers report the raw message (e.g. ``terminated``) while the
local Pi runner wraps model errors as ``Pi model call failed: ...``.
Unrecognized failures classify as ``unknown`` — never default to ``technical``.

Category semantics agreed with operators:

- ``business``: the node ran to completion but its output failed content or
  contract review, or the source material itself is unusable (bad subtitles,
  missing video) — retrying unchanged infrastructure will not help.
- ``technical``: infrastructure, configuration, or environment broke —
  fixing the cause and rerunning the same node should succeed. ``Missing
  outputs`` counts as technical: with a mature workflow a finished run that
  produced no artifacts almost always means the pipeline, not the content.
"""

from __future__ import annotations

import re

CATEGORY_TECHNICAL = "technical"
CATEGORY_BUSINESS = "business"
CATEGORY_UNKNOWN = "unknown"
FAILURE_CATEGORIES = (CATEGORY_TECHNICAL, CATEGORY_BUSINESS, CATEGORY_UNKNOWN)

_REVIEW_REJECTED_MARKERS = (
    "review_rejected:",
    "content review rejected by skill",
    "Output validation failed: Review rejected",
)
_PI_MODEL_CALL_PREFIX = "Pi model call failed:"
_MISSING_OUTPUTS_PREFIXES = ("missing outputs", "missing required file")
_NO_OUTPUT_ARTIFACTS_PREFIX = "Agent Worker did not report output artifacts"
_UNPACK_FAILURE = "failed to unpack Agent result"
_RESOURCE_LIMIT_MARKERS = ("Too many open files", "No space left on device")
_SQLITE_MARKERS = (
    "database is locked",
    "cannot rollback",
    "unable to open database file",
    "database or disk is full",
)
_EXECUTION_ERROR_MARKERS = (
    "openclaw command failed",
    "SQLite objects created in a thread",
    "isolated handler did not return a result",
    "object has no attribute",
    "[Errno 2] No such file or directory",
)
_NETWORK_MARKERS = ("IncompleteRead", "ChunkedEncodingError", "Connection broken")
_PROCESS_EXITED_RE = re.compile(r"^Agent process exited (\d+)$")
_TERMINATED_WORD_RE = re.compile(r"\bterminated\b")
_EXECUTOR_NOT_REGISTERED_RE = re.compile(r"^Executor '.+' is not registered$")
_INTERACTION_CONTRACT_RE = re.compile(r"^Interaction \d+.*(is missing|has unknown type)")

TIMEOUT_EXIT_CODE = 124


def classify_failure(exit_code: int | None, error_message: str) -> tuple[str, str]:
    """Map one failed run's exit code and message to (category, detail)."""
    message = error_message or ""

    if message.startswith(_REVIEW_REJECTED_MARKERS):
        return CATEGORY_BUSINESS, "review_rejected"

    # Business: output quality / contract violations and unusable source data.
    if message.startswith("Output validation failed:"):
        return CATEGORY_BUSINESS, "output_invalid"
    if message.startswith("invalid json ") or _INTERACTION_CONTRACT_RE.match(message):
        return CATEGORY_BUSINESS, "output_invalid"
    if "All transcription providers failed" in message:
        return CATEGORY_BUSINESS, "transcription_input"
    if "HTTPError: 404" in message:
        return CATEGORY_BUSINESS, "source_missing"

    exited = _PROCESS_EXITED_RE.match(message)
    if (
        exit_code == TIMEOUT_EXIT_CODE
        or (exited is not None and exited.group(1) == "124")
        or "timed out" in message
    ):
        return CATEGORY_TECHNICAL, "timeout"

    if _EXECUTOR_NOT_REGISTERED_RE.match(message):
        return CATEGORY_TECHNICAL, "executor_unregistered"

    if message.startswith(
        ("worker interrupted before restart", "lease expired", "Agent Worker heartbeat expired")
    ):
        return CATEGORY_TECHNICAL, "worker_orphaned"

    # Provider stream interruptions, raw (Worker-reported) or Pi-runner-wrapped.
    if _TERMINATED_WORD_RE.search(message) or "Connection error" in message:
        return CATEGORY_TECHNICAL, "provider_stream"

    if message.startswith(_PI_MODEL_CALL_PREFIX) or "HTTPError: 502" in message:
        return CATEGORY_TECHNICAL, "provider_request"

    if "CmsClientError" in message or "CMS token" in message:
        return CATEGORY_TECHNICAL, "cms_auth"

    if any(marker in message for marker in _RESOURCE_LIMIT_MARKERS):
        return CATEGORY_TECHNICAL, "resource_limit"

    # Technical: operator cancellations, routing/skill setup, transfer and
    # environment failures.
    if message.startswith("execution was cancelled"):
        return CATEGORY_TECHNICAL, "cancelled"

    if message.startswith(("No Executor binding", "workspace node is not routed to an Agent")):
        return CATEGORY_TECHNICAL, "executor_binding"

    # Sweeper-failed queued requests whose pinned Agent definition changed.
    if message.startswith("Agent definition '") and "was disabled or changed" in message:
        return CATEGORY_TECHNICAL, "stale_definition"

    if "config differs from skills.lock" in message or message.startswith(
        ("skill missing references", "git command failed")
    ):
        return CATEGORY_TECHNICAL, "skill_config"

    if message.startswith("artifact upload failed") or "download failed: /api/" in message:
        return CATEGORY_TECHNICAL, "artifact_transfer"

    if message.startswith("lease was lost during execution"):
        return CATEGORY_TECHNICAL, "lease_lost"

    if any(marker in message for marker in _SQLITE_MARKERS):
        return CATEGORY_TECHNICAL, "database"

    if any(marker in message for marker in _NETWORK_MARKERS):
        return CATEGORY_TECHNICAL, "network"

    if message.lower().startswith(_MISSING_OUTPUTS_PREFIXES) or message.startswith(
        _NO_OUTPUT_ARTIFACTS_PREFIX
    ):
        return CATEGORY_TECHNICAL, "output_missing"

    if _UNPACK_FAILURE in message or any(marker in message for marker in _EXECUTION_ERROR_MARKERS):
        return CATEGORY_TECHNICAL, "execution_error"

    if exited is not None:
        return CATEGORY_UNKNOWN, "execution_error"

    return CATEGORY_UNKNOWN, "unknown"
