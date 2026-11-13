"""Safe Host diagnostics for Agent result-metadata HTTP 400 responses."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from server.app.routes.agent_worker_result_shapes import prespool_metadata, read_member

logger = logging.getLogger(__name__)

_INVALID_DETAIL = "invalid Agent result metadata"
_MAX_EXECUTION_ID_CHARS = 64
_SAFE_REASONS = frozenset(
    {
        "metadata is not valid JSON",
        "metadata must be a JSON object",
        "invalid status",
        "invalid exit_code",
        "invalid command",
        "invalid output artifacts",
        "invalid output artifact reference",
        "invalid output artifact storage key",
        "invalid output artifact size",
        "invalid output artifact content hash",
        "invalid run_dir",
        "invalid auth_failure_connection",
        "archived result metadata member is too large",
        "archived result metadata member is not a file",
        "archived result metadata member is not valid UTF-8",
        "archived result metadata member is not valid JSON",
        "archived result metadata member is missing",
        "result archive is unreadable",
    }
)


def safe_result_rejection_detail(detail: Any) -> str:
    """Keep only protocol-owned static diagnostics, never Worker payload text.

    The HTTP response remains unchanged. The log face is fail-closed for new
    or dynamic exception messages: unknown details collapse to the fixed
    category instead of echoing possible credentials or unbounded data.
    """
    text = str(detail)
    if text == _INVALID_DETAIL:
        return text
    prefix, separator, reason = text.partition(": ")
    if prefix != _INVALID_DETAIL or not separator:
        return _INVALID_DETAIL
    if reason.startswith("result archive is unreadable:"):
        reason = "result archive is unreadable"
    if reason not in _SAFE_REASONS:
        return _INVALID_DETAIL
    return f"{_INVALID_DETAIL}: {reason}"


def prespool_metadata_logged(execution_id: str, request: Request) -> Any:
    try:
        return prespool_metadata(request)
    except HTTPException as exc:
        log_result_rejection(execution_id, exc)
        raise


async def read_member_logged(execution_id: str, staged: Path) -> Any:
    try:
        return await read_member(staged)
    except HTTPException as exc:
        log_result_rejection(execution_id, exc)
        raise


def log_result_rejection(execution_id: str, exc: HTTPException) -> None:
    """Log one bounded, single-line 400 diagnostic; leave other statuses alone."""
    if exc.status_code != 400:
        return
    identity = str(execution_id)[:_MAX_EXECUTION_ID_CHARS]
    logger.warning(
        "Agent result metadata rejected: execution_id=%r detail=%s",
        identity,
        safe_result_rejection_detail(exc.detail),
    )
