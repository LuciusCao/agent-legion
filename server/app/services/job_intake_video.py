from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.security import validate_download_url
from server.app.services.job_errors import InvalidOperationError, UnsupportedOperationError
from server.app.services.job_intake_resolution import candidate


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


def resolve_cms_video_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
    resolver: str,
    cms_config: dict[str, Any],
) -> list[dict[str, Any]]:
    if entity != "video":
        raise UnsupportedOperationError(f"{entity} resolver not yet implemented")

    if resolver != "cms.knowledge_video":
        raise UnsupportedOperationError(f"Unsupported video resolver: {resolver}")

    api_url = cms_config.get("knowledge_url")
    token = get_token(str(cms_config.get("env", "")), cms_config)

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code in input_values:
        lookup = lookup_knowledge_video(code, api_url, token)
        if lookup.status == "not_found":
            continue
        if code in seen:
            continue
        seen.add(code)
        candidates.append(
            candidate(
                "video",
                code,
                lookup.title or f"Video {code}",
                source_kind,
                code,
                source_url=lookup.url,
                source_uuid=lookup.source_uuid,
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


def write_video_input(job_dir: Path, candidate: dict[str, Any]) -> None:
    video_input = {
        "schema_version": 1,
        "entity_type": "video",
        "content_type": str(candidate.get("content_type") or "knowledge"),
        "legacy_video_id": "",
        "external_id": str(candidate.get("external_id") or candidate["entity_id"]),
        "source_uuid": str(candidate.get("source_uuid") or ""),
        "source_url": str(candidate.get("source_url") or ""),
        "title": str(candidate["title"]),
    }
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(video_input, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
