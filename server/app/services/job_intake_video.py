from __future__ import annotations

import logging
from typing import Any

from server.app.security import validate_download_url
from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_resolution import candidate

logger = logging.getLogger(__name__)


def resolve_video_url_candidates(
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in input_values:
        try:
            validate_download_url(value)
        except ValueError as exc:
            raise InvalidOperationError(f"Invalid video URL: {exc}") from exc
        candidates.append(
            candidate(
                "video",
                value,
                f"Video {value}",
                source_kind,
                value,
                source_url=value,
                source_uuid="",
                content_type="knowledge",
            )
        )
    return candidates


def exclude_existing_candidates(
    candidates: list[dict[str, Any]],
    existing_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in candidates
        if (str(candidate["entity_type"]), str(candidate["entity_id"])) not in existing_keys
    ]
