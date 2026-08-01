"""Model-call failure detection for Pi ``--mode json`` event streams.

Pi can exit with code 0 even when the upstream model request fails (e.g. a
400 from the provider). Split out of ``pi_protocol.py`` to keep that module
under its size budget; ``pi_protocol`` re-exports both names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def fold_model_error(event: dict[str, Any], last_error: str | None) -> str | None:
    """Fold one parsed Pi event into the running model-error state.

    An assistant message carrying ``errorMessage`` sets the error; a later
    assistant message ending with ``stopReason`` ``stop``/``toolUse``
    clears it (Pi auto-retries transient failures, so only an unrecovered
    error counts). Returns the updated state.
    """
    messages: list[dict[str, Any]] = []
    # message_start / message_end / turn_end wrap the assistant msg
    msg = event.get("message") or {}
    if not isinstance(msg, dict):
        turn_end = event.get("turn_end") or {}
        msg = turn_end.get("message") if isinstance(turn_end, dict) else {}
    if isinstance(msg, dict):
        messages.append(msg)

    # message_update events nest under assistantMessageEvent
    assistant_event = event.get("assistantMessageEvent") or {}
    if isinstance(assistant_event, dict):
        nested = assistant_event.get("message") or {}
        if isinstance(nested, dict):
            messages.append(nested)

    for msg in messages:
        if msg.get("errorMessage"):
            last_error = str(msg["errorMessage"])
        elif msg.get("stopReason") in ("stop", "toolUse"):
            last_error = None
    return last_error


def detect_model_error(events_file: Path) -> str | None:
    """Scan Pi JSONL events for model-call failures reported by the CLI.

    The events file contains assistant messages whose ``stopReason`` is
    ``error`` and which carry an ``errorMessage``. Detecting this prevents
    us from reporting a misleading "Missing outputs" error when the agent
    never had a chance to run.

    Pi auto-retries transient failures (e.g. "terminated"), so an error only
    counts when no later assistant message succeeds; recovered retries pass.
    """
    if not events_file.is_file():
        return None
    last_error: str | None = None
    try:
        with events_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                last_error = fold_model_error(event, last_error)
    except Exception:
        return None
    return last_error
