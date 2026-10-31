"""Deterministic structured JSON budgeting for diagnostic evidence."""

from __future__ import annotations

import copy
import json
from typing import Any


def _dump(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clip(value: Any, width: int) -> Any:
    if isinstance(value, str):
        return value[-width:]
    if isinstance(value, list):
        return [_clip(item, width) for item in value]
    if isinstance(value, dict):
        return {key: _clip(item, width) for key, item in value.items()}
    return value


def _event_lists(document: Any) -> list[tuple[dict[str, Any], str, list[dict[str, Any]]]]:
    result = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "preload_events" and isinstance(item, list):
                    result.append((value, key, item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return result


def _priority(event: dict[str, Any], index: int, last: int) -> int:
    failure = "error" in event or any(
        event.get(key) not in (None, 0, "0", "") for key in ("errno", "result")
    )
    if index in (0, last) or failure:
        return 0
    name = event.get("event", "")
    return 1 if isinstance(name, str) and name.startswith(("mount_", "setns_")) else 2


def _select_events(document: Any, keep: int) -> Any:
    result = copy.deepcopy(document)
    lists = _event_lists(result)
    entries = [
        (_priority(event, index, len(events) - 1), list_index, index)
        for list_index, (_, _, events) in enumerate(lists)
        for index, event in enumerate(events)
    ]
    selected = {(list_index, index) for _, list_index, index in sorted(entries)[:keep]}
    for list_index, (parent, key, events) in enumerate(lists):
        parent[key] = [
            event for index, event in enumerate(events) if (list_index, index) in selected
        ]
        omitted = len(events) - len(parent[key])
        if omitted:
            parent[f"{key}_omitted"] = omitted
    return result


def bounded_json(document: dict[str, Any], limit: int) -> str:
    clipped = document
    for width in (128, 64, 32, 16):
        clipped = _clip(document, width)
        text = _dump(clipped)
        if len(text.encode("utf-8")) <= limit:
            return text
    total = sum(len(events) for _, _, events in _event_lists(clipped))
    best = None
    low, high = 0, total - 1
    while low <= high:
        middle = (low + high) // 2
        text = _dump(_select_events(clipped, middle))
        if len(text.encode("utf-8")) <= limit:
            best, low = text, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError(f"diagnostic JSON cannot fit within {limit} UTF-8 bytes")
    return best
