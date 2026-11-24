"""Kimi Code 1.52 wire-journal compaction signals (#1025).

The ACP adapter still drops these events. The journal's protocol 1.10 records
instead carry ``timestamp`` plus ``message: {type, payload}``. Only that
verified envelope version is interpreted; legacy and unknown versions remain
neutral and retain the ACP text-marker fallback. Journal baselines, not record
timestamps, decide what is history.

``CompactionEnd`` has no outcome or token fields. It only closes the window.
Context usage comes solely from a structured ``StatusUpdate`` (or the existing
ACP ``usage_update`` path), never from message text or ``TurnEnd``.
"""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, Any

from server.app.studio_chat import compact_markers, compaction

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

SUPPORTED_PROTOCOL_VERSIONS = frozenset({"1.10"})
_HEADER_READ_BYTES = 4096
_MAX_TOKEN_COUNT = (1 << 31) - 1
_WIRE_START_TEXT = "Compacting conversation context"
_WIRE_DONE_TEXT = "Compaction completed."

Signal = tuple[str, str, dict[str, Any]]


def read_protocol_version(descriptor: int) -> str | None:
    """Read only the bounded metadata header from an already-safe descriptor."""
    data = os.pread(descriptor, _HEADER_READ_BYTES, 0)
    end = data.find(b"\n")
    if end < 0:
        return None
    try:
        header = json.loads(data[:end])
    except (ValueError, RecursionError):
        return None
    if not isinstance(header, dict) or header.get("type") != "metadata":
        return None
    version = header.get("protocol_version")
    return version if isinstance(version, str) and len(version) <= 32 else None


def parse_records(data: bytes) -> list[dict[str, Any]]:
    """Parse complete JSONL records; malformed and non-object lines are neutral."""
    records: list[dict[str, Any]] = []
    for line in data.split(b"\n"):
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def text_blocks(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return [{"type": "content", "content": {"type": "text", "text": text}}]


def project(record: dict[str, Any], protocol_version: str | None) -> list[Signal]:
    """Project one untrusted record without scanning text or nesting deeply."""
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return []
    timestamp = record.get("timestamp")
    if type(timestamp) not in (int, float):
        return []
    if isinstance(timestamp, float) and not math.isfinite(timestamp):
        return []
    message = record.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("payload"), dict):
        return []
    kind, payload = message.get("type"), message["payload"]
    if kind in ("CompactionBegin", "CompactionEnd"):
        if payload:
            return []
        marker = "start" if kind == "CompactionBegin" else "done"
        return [("compaction", "system", {"marker": marker})]
    if kind != "StatusUpdate":
        return []
    used, size = payload.get("context_tokens"), payload.get("max_context_tokens")
    if type(used) is not int or type(size) is not int:
        return []
    if not 0 <= used <= _MAX_TOKEN_COUNT or not 0 < size <= _MAX_TOKEN_COUNT:
        return []
    return [("usage", "system", {"used": used, "size": size})]


def apply(
    backend: ServiceBackend,
    session_id: str,
    runtime: SessionRuntime,
    kind: str,
    content: dict[str, Any],
) -> bool:
    """Apply a projected signal through the existing compaction/usage paths."""
    if kind == "compaction":
        retry = content.pop("_attempted", False)
        content["_attempted"] = True
        marker = content["marker"]
        if not retry:
            # The watcher holds runtime.lock. Preserve this head signal's
            # first-attempt intent so a retried duplicate stays a duplicate.
            content["_notify"] = not (
                (runtime.compacting and marker == "start")
                or (runtime.compaction_done and marker == "done")
            )
        text = _WIRE_START_TEXT if marker == "start" else _WIRE_DONE_TEXT
        compact_markers.apply_marker_gated(
            backend,
            session_id,
            runtime,
            marker,
            text,
            timeout=compaction.COMPACTING_TIMEOUT_SECONDS,
            structured=True,
            retry=retry and content["_notify"],
        )
        return True
    if kind != "usage":
        return False
    usage = (backend.db.get_studio_chat_session(session_id) or {}).get("usage")
    update = dict(content)
    if isinstance(usage, dict) and isinstance(usage.get("cost"), dict):
        update["cost"] = usage["cost"]
    backend.db.update_studio_chat_session(session_id, usage=update)
    backend.store.publish_session(session_id)
    return True
