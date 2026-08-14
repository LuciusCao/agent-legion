"""Serialization helpers for Studio chat SSE payloads and permission options."""

from __future__ import annotations

from typing import Any


def pick_allow_option(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the option an auto-approval selects: allow_always, then
    allow_once, then the first offered option."""
    for preferred in ("allow_always", "allow_once"):
        for option in options:
            if option.get("kind") == preferred:
                return option
    return options[0] if options else None


def serialize_message(message: dict[str, Any]) -> dict[str, Any]:
    return {**message, "created_at": str(message.get("created_at"))}


def serialize_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (str(value) if key.endswith("_at") and value is not None else value)
        for key, value in session.items()
    }
