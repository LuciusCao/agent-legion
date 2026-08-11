"""Rule table mapping a failed run's exit code and message to (category, detail).

Message markers live in ``_failure_classification_markers``. Unrecognized
failures classify as ``unknown`` — never default to ``technical``.

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

from server.app.services._failure_classification_markers import (
    _CMS_TRANSPORT_PREFIX,
    _CONNECTION_CONFIG_RE,
    _DB_POOL_MARKERS,
    _EXECUTION_ERROR_MARKERS,
    _EXECUTOR_NOT_REGISTERED_RE,
    _INTERACTION_CONTRACT_RE,
    _MISSING_OUTPUTS_PREFIXES,
    _NETWORK_MARKERS,
    _NO_OUTPUT_ARTIFACTS_PREFIX,
    _PI_MODEL_CALL_PREFIX,
    _PROCESS_EXITED_RE,
    _PROVIDER_CALL_PREFIX,
    _PROVIDER_CALL_STREAM_MARKERS,
    _PROVIDER_CONTENT_FILTER_MARKER,
    _RESOURCE_LIMIT_MARKERS,
    _REVIEW_REJECTED_MARKERS,
    _SOURCE_MISSING_MARKERS,
    _SQLITE_MARKERS,
    _TERMINATED_WORD_RE,
    _UNPACK_FAILURE,
)

CATEGORY_TECHNICAL = "technical"
CATEGORY_BUSINESS = "business"
CATEGORY_UNKNOWN = "unknown"
FAILURE_CATEGORIES = (CATEGORY_TECHNICAL, CATEGORY_BUSINESS, CATEGORY_UNKNOWN)

TIMEOUT_EXIT_CODE = 124

DETAIL_DB_POOL_TIMEOUT = "db_pool_timeout"
DETAIL_CMS_REQUEST = "cms_request"
TRANSIENT_RETRY_DETAILS = frozenset({DETAIL_DB_POOL_TIMEOUT, DETAIL_CMS_REQUEST})


def classify_failure(exit_code: int | None, error_message: str) -> tuple[str, str]:
    """Map one failed run's exit code and message to (category, detail)."""
    message = error_message or ""

    if message.startswith(_REVIEW_REJECTED_MARKERS):
        return CATEGORY_BUSINESS, "review_rejected"

    # CMS transport failures (5xx/timeout/DNS) are transient: the lease
    # finish path hands the node back to the claimable set instead of
    # failing the job. Checked before the timeout rule — a CMS read timeout
    # message also contains "timed out".
    if message.startswith(_CMS_TRANSPORT_PREFIX):
        return CATEGORY_TECHNICAL, DETAIL_CMS_REQUEST

    # Dispatch-time external-connection failures (missing/disabled/token
    # acquisition): fix the connection in admin settings, then rerun.
    # Checked early — the wrapped cause may itself contain "timed out" or
    # "Connection error".
    if _CONNECTION_CONFIG_RE.match(message):
        return CATEGORY_TECHNICAL, "connection_config"

    # Business: output quality / contract violations and unusable source data.
    if message.startswith("Output validation failed:"):
        return CATEGORY_BUSINESS, "output_invalid"
    # Technical: the validator itself failed to run (environment/setup fault),
    # as opposed to the output failing the contract above.
    if message.startswith("Validator error:"):
        return CATEGORY_TECHNICAL, "validator_env"
    if message.startswith("invalid json ") or _INTERACTION_CONTRACT_RE.match(message):
        return CATEGORY_BUSINESS, "output_invalid"
    if "All transcription providers failed" in message:
        return CATEGORY_BUSINESS, "transcription_input"
    if any(marker in message for marker in _SOURCE_MISSING_MARKERS):
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

    # velites provider errors: content-filter stops are business, stream or
    # transport interruptions are provider_stream, the rest provider_request.
    if message.startswith(_PROVIDER_CALL_PREFIX):
        if _PROVIDER_CONTENT_FILTER_MARKER in message:
            return CATEGORY_BUSINESS, "provider_content_filter"
        if any(marker in message for marker in _PROVIDER_CALL_STREAM_MARKERS):
            return CATEGORY_TECHNICAL, "provider_stream"
        return CATEGORY_TECHNICAL, "provider_request"

    # In-band CMS errors (auth/parameter, "CMS 返回错误: code=…") and token
    # acquisition failures; transport failures classified as cms_request above.
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

    # Manifest frozen without a routable provider/model (node override and
    # workspace default both missing at enqueue): configure Settings or the
    # node execution and rerun — covers both the legacy ("unresolved model")
    # and current ("unresolved provider/model") guard wording.
    if message.startswith("Agent request manifest has unresolved"):
        return CATEGORY_TECHNICAL, "unresolved_model"

    if "config differs from the published skill lock" in message or message.startswith(
        ("skill missing references", "git command failed")
    ):
        return CATEGORY_TECHNICAL, "skill_config"

    if message.startswith("artifact upload failed") or "download failed: /api/" in message:
        return CATEGORY_TECHNICAL, "artifact_transfer"

    if message.startswith("lease was lost during execution"):
        return CATEGORY_TECHNICAL, "lease_lost"

    if any(marker in message for marker in _SQLITE_MARKERS):
        return CATEGORY_TECHNICAL, "database"

    if any(marker in message for marker in _DB_POOL_MARKERS):
        return CATEGORY_TECHNICAL, DETAIL_DB_POOL_TIMEOUT

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
