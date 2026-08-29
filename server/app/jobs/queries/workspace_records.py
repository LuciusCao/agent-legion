"""Workspace row JSON-column decode helpers.

Split from queries/workspace.py for the architecture file budget; the record
shape (decoded *_config dicts alongside the raw *_json columns) is shared by
every workspace read path.
"""

from __future__ import annotations

import json
from typing import Any


def _decode_json_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def workspace_record(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["resource_config"] = _decode_json_object(record.get("resource_config_json"))
    record["node_config"] = _decode_json_object(record.get("node_config_json"))
    record["preview_config"] = _decode_json_object(record.get("preview_config_json"))
    return record
