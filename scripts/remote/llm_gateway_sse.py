"""Recognition of terminal OpenAI-compatible SSE events."""

import json

SSE_DONE_MARKER = b"[DONE]"


def line_is_terminal(line: bytes) -> tuple[bool, bool]:
    """Return whether an SSE data line completes a response and is [DONE]."""
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return False, False
    payload = stripped.removeprefix(b"data:").strip()
    if payload == SSE_DONE_MARKER:
        return True, True
    try:
        document = json.loads(payload)
        choices = document.get("choices", [])
        return any(choice.get("finish_reason") is not None for choice in choices), False
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError):
        return False, False
