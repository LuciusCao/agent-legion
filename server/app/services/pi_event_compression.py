from __future__ import annotations

from pathlib import Path

from server.app.services.pi_event_scan import (
    RELEVANT_EVENT_TYPES,
    scan_and_compress_pi_events,
)

__all__ = ["RELEVANT_EVENT_TYPES", "compress_pi_events", "scan_and_compress_pi_events"]


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed = scan_and_compress_pi_events(events_path)
    return original, compressed
