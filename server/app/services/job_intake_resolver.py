from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_resolution import (
    resolve_cms_question_candidates,
    resolve_direct_candidates,
)
from server.app.services.job_intake_video import resolve_cms_video_candidates
from server.app.settings import Settings


def resolve_candidates(
    resolver: str,
    entity: str,
    input_values: list[str],
    source_kind: str,
    cms_config: dict[str, Any],
    mode: Any,
    settings: Settings,
    workspace: dict[str, Any],
    workspace_id: str,
    dedup_state: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    if resolver.startswith("direct."):
        return resolve_direct_candidates(entity, input_values, source_kind)
    if resolver.startswith("cms.") and entity == "video":
        return resolve_cms_video_candidates(
            entity, input_values, source_kind, resolver, cms_config, dedup_state=dedup_state
        )
    if resolver.startswith("cms."):
        return resolve_cms_question_candidates(
            entity, input_values, source_kind, resolver, mode, settings, workspace, workspace_id
        )
    raise InvalidOperationError(f"Unsupported resolver: {resolver}")
