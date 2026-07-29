from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_errors import UnsupportedOperationError

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


def resolve_cms_question_opaque_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    """Build opaque question candidates without calling CMS.

    Node-phase resolution: the first DAG node (``workflow_nodes/question_intake.py``)
    resolves ids / knowledge codes against the CMS at execution time (binding +
    vault chain), so intake only fans out one candidate per input value,
    deduped by value.
    """
    if entity != "question":
        raise UnsupportedOperationError(f"{entity} resolver not yet implemented")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in input_values:
        if value in seen:
            continue
        seen.add(value)
        candidates.append(
            candidate(
                entity,
                value,
                f"Question {value}",
                source_kind,
                value,
                source_ref=value,
            )
        )
    return candidates
