"""Chunked candidate resolution for job intake.

Resolving tens of thousands of input values in one call materializes the
full candidate list before any dedup filtering happens. Resolving in bounded
chunks and filtering each chunk against the workspace dedup keys keeps the
transient candidate buffer small; cross-batch and intra-request dedup
semantics are unchanged.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_intake_resolver import resolve_candidates
from server.app.services.job_intake_video import exclude_existing_candidates
from server.app.settings import Settings

INTAKE_RESOLUTION_CHUNK_SIZE = 500


def resolve_fresh_candidates(
    resolver: str,
    entity: str,
    input_values: list[str],
    source_kind: str,
    cms_config: dict[str, Any],
    mode: Any,
    settings: Settings,
    workspace: dict[str, Any],
    workspace_id: str,
    existing_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve ``input_values`` in chunks and return candidates not already known.

    ``existing_keys`` starts as the workspace dedup key set and grows with
    every accepted candidate, so duplicates across chunk boundaries within
    the same request are filtered exactly like pre-existing jobs.
    ``dedup_state`` keeps resolver-level dedup (e.g. shared video URLs)
    effective across chunks. Returns the accepted candidates and whether any
    candidate was resolved at all (pre-filter).
    """
    candidates: list[dict[str, Any]] = []
    resolved_any = False
    dedup_state: dict[str, set[str]] = {}
    for start in range(0, len(input_values), INTAKE_RESOLUTION_CHUNK_SIZE):
        chunk = resolve_candidates(
            resolver,
            entity,
            input_values[start : start + INTAKE_RESOLUTION_CHUNK_SIZE],
            source_kind,
            cms_config,
            mode,
            settings,
            workspace,
            workspace_id,
            dedup_state=dedup_state,
        )
        resolved_any = resolved_any or bool(chunk)
        fresh = exclude_existing_candidates(chunk, existing_keys)
        for candidate in fresh:
            existing_keys.add((str(candidate["entity_type"]), str(candidate["entity_id"])))
        candidates.extend(fresh)
    return candidates, resolved_any
