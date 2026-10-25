"""Task-center read derivation for batch operations' feeder watermark."""

from __future__ import annotations

import json
from typing import Any

from server.app.services.run_batches.feeding_gate import (
    WATERMARK_JOB_BATCH_OPERATION_KINDS,
    feeding_gate_for_row,
)


def operation_feeding_gate(
    snapshot: dict[str, int] | None, row: dict[str, Any]
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    payload = json.loads(str(row["target_spec_json"] or "{}"))
    stream = payload.get("stream")
    has_remaining_work = int(row["next_index"]) < int(row["items_total"]) or (
        stream is not None and stream.get("next_cursor") is not None
    )
    return feeding_gate_for_row(
        snapshot,
        row,
        watermark_applies=str(row["kind"]) in WATERMARK_JOB_BATCH_OPERATION_KINDS,
        has_remaining_work=has_remaining_work,
    )
