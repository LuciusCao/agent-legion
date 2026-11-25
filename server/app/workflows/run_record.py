"""Public run-record projection with internal resource pins removed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.db.rowmap import iso_optional, parse_object
from server.app.workflows.shared_resources import (
    public_resources_from_run,
    without_internal_pins,
)


def run_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "source_kind": str(row["source_kind"]),
        "status": str(row["status"]),
        "created_count": int(row["created_count"]),
        "error_message": str(row["error_message"] or ""),
        "frozen_pins": without_internal_pins(row),
        "shared_resources": public_resources_from_run(row),
        "stats": parse_object(row.get("stats_json")),
        "created_by": str(row["created_by"] or ""),
        "created_at": iso_optional(row["created_at"]),
        "updated_at": iso_optional(row["updated_at"]),
    }
