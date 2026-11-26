"""Chunked frozen-input reads for run-item dedup (#1565).

Separated from ``run_item_probes`` for its file budget. The query mirrors the
existing dedup-key probe exactly, including its resource-pins namespace, but
returns the actual hit rows' ``input_json`` so callers never infer a job id.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_DEDUP_INPUT_SQL = (
    "select source_type, source_id, input_json from jobs"
    " where workspace_id=%s and (source_type, source_id) in ({pairs})"
)
_DEDUP_RESOURCE_INPUT_SQL = (
    "select j.source_type, j.source_id, j.input_json from jobs j join runs r on r.id=j.run_id"
    " where j.workspace_id=%s and (j.source_type, j.source_id) in ({pairs})"
    " and r.frozen_pins_json::jsonb #>> '{{shared_resources,pins_hash}}'=%s"
)
_PROBE_CHUNK = 500


def fetch_dedup_inputs(
    conn: Any,
    workspace_id: str,
    keys: Iterable[tuple[str, str]],
    resource_pins_hash: str = "",
) -> list[dict[str, Any]]:
    """Return actual dedup-hit rows, preserving the key probe's pin semantics."""
    pending = list(dict.fromkeys(keys))
    existing: list[dict[str, Any]] = []
    for start in range(0, len(pending), _PROBE_CHUNK):
        chunk = pending[start : start + _PROBE_CHUNK]
        placeholders = ",".join("(%s,%s)" for _ in chunk)
        params: list[str] = [workspace_id]
        for pair in chunk:
            params.extend(pair)
        sql = _DEDUP_RESOURCE_INPUT_SQL if resource_pins_hash else _DEDUP_INPUT_SQL
        if resource_pins_hash:
            params.append(resource_pins_hash)
        rows = conn.execute(sql.format(pairs=placeholders), params).fetchall()
        existing.extend(dict(row) for row in rows)
    return existing
