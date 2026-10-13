"""Host-facing face of the Pi events.jsonl compression pipeline.

The scan state machine lives in ``shared/pi_events_scan.py`` (budget
reclaim, closed anchors #843/#844); this module keeps the thin
``compress_pi_events`` wrapper the Host write paths import and re-exports
the scan entry points so existing callers and doc references stay valid.
"""

from __future__ import annotations

from pathlib import Path

from shared.pi_events_scan import (
    MODEL_ERROR_REDACTION_FAILED,
    RELEVANT_EVENT_TYPES,
    scan_and_compress_pi_events,
)

__all__ = [
    "MODEL_ERROR_REDACTION_FAILED",
    "RELEVANT_EVENT_TYPES",
    "compress_pi_events",
    "scan_and_compress_pi_events",
]


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed, _ = scan_and_compress_pi_events(events_path)
    return original, compressed
