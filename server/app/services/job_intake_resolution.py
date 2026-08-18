from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def candidate(
    entity_type: str,
    entity_id: str,
    title: str,
    source_kind: str,
    source_value: str,
    stem: str = "",
    **extras: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "stem": stem,
        "source": {"kind": source_kind, "value": source_value},
    }
    result.update(extras)
    return result


def normalize_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def resolve_direct_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    if entity == "video" and source_kind == "batch_by_urls":
        from server.app.services.job_intake_video import resolve_video_url_candidates

        return resolve_video_url_candidates(input_values, source_kind)
    return [
        candidate(entity, value, f"{entity.title()} {value}", source_kind, value)
        for value in input_values
    ]


def resolve_opaque_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    """Fan out opaque candidates carrying ``source_ref`` for node-phase resolution.

    Intake does not talk to any external service here: the first DAG node
    resolves ``source_ref`` against its configured connection at execution
    time, so intake only fans out one deduped candidate per input value.
    """
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for value in normalize_values(input_values):
        if value in seen:
            continue
        seen.add(value)
        extras: dict[str, Any] = {"source_ref": value}
        if entity == "video":
            extras.update(source_url="", source_uuid="", content_type="knowledge")
        candidates.append(
            candidate(entity, value, f"{entity.title()} {value}", source_kind, value, **extras)
        )
    return candidates
