"""Command extraction from ACP tool-call payloads (#954; split from
tool_call_commands.py, file budget).

Two shapes carry a command: ``rawInput.command`` (the request itself, or
kimi's ``tool.call.started`` notification), and kimi's streamed-args
notifications, whose text content is the *cumulative* argument JSON — only a
complete JSON object parses, so a parsed ``command`` means the stream is
over.
"""

from __future__ import annotations

import json
from typing import Any


def declared_command(tool_call: dict[str, Any]) -> str | None:
    """``rawInput.command`` (stripped), or None when absent/blank/not a string."""
    raw_input = tool_call.get("rawInput")
    return _command(raw_input.get("command") if isinstance(raw_input, dict) else None)


def streamed_command(update: dict[str, Any]) -> str | None:
    """``command`` of the cumulative args text once it is complete JSON."""
    content = update.get("content")
    for entry in content if isinstance(content, list) else ():
        inner = entry.get("content") if isinstance(entry, dict) else None
        text = inner.get("text") if isinstance(inner, dict) else None
        if not isinstance(text, str):
            continue
        try:
            args = json.loads(text)
        except ValueError:
            continue  # stream still in flight (or not args at all)
        if isinstance(args, dict) and (command := _command(args.get("command"))):
            return command
    return None


def _command(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
