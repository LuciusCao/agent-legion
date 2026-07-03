from __future__ import annotations

import json
import logging
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger(__name__)


# Event types that the job log renderer consumes.  All message_update deltas
# (thinking_delta, text_delta, toolcall_delta, ...) are discarded because the
# final state is captured in message_end events.
_RELEVANT_EVENT_TYPES = frozenset(
    {
        "session",
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "message_start",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
    }
)


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    if not events_path.is_file():
        return 0, 0

    original_size = events_path.stat().st_size
    if original_size == 0:
        return 0, 0

    compressed_path = events_path.with_suffix(".jsonl.compressing")
    try:
        with (
            events_path.open("r", encoding="utf-8", errors="replace") as src,
            compressed_path.open("w", encoding="utf-8") as dst,
        ):
            for raw_line in src:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") in _RELEVANT_EVENT_TYPES:
                    dst.write(line + "\n")
    except Exception:
        logger.exception("Failed to compress Pi events: %s", events_path)
        with suppress(OSError):
            compressed_path.unlink(missing_ok=True)
        return 0, 0

    try:
        compressed_path.replace(events_path)
    except OSError:
        logger.exception("Failed to replace events file: %s", events_path)
        return 0, 0

    compressed_size = events_path.stat().st_size
    return original_size, compressed_size
