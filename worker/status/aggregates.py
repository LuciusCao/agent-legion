"""Helpers for aggregating Worker runtime status from volatile execution state."""

from __future__ import annotations

from typing import Any


def execution_counts(executions: list[dict[str, Any]]) -> dict[str, int]:
    """Return running / queued_upload / uploading counts from execution entries."""
    running = queued = uploading = 0
    for entry in executions:
        phase = entry.get("phase")
        if phase in ("claimed", "downloading", "running"):
            running += 1
        elif phase == "queued_upload":
            queued += 1
        elif phase == "uploading":
            uploading += 1
    return {
        "running_executions_count": running,
        "upload_queued_count": queued,
        "upload_active_count": uploading,
    }
