"""Task-center read derivation for batch operations' feeder watermark."""

from __future__ import annotations

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
    return feeding_gate_for_row(
        snapshot,
        row,
        watermark_applies=str(row["kind"]) in WATERMARK_JOB_BATCH_OPERATION_KINDS,
    )
