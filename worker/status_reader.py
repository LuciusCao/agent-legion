"""Reader side of the volatile Worker runtime status file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_runtime_status(path: Path) -> dict[str, Any]:
    """Read live child state; return empty state for dead, missing, or corrupt writers."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        os.kill(int(payload["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return {"executions": [], "remote": {}}
    executions = payload.get("executions")
    if not isinstance(executions, dict):
        executions = {}
    remote = payload.get("remote")
    return {
        "executions": sorted(
            (entry for entry in executions.values() if isinstance(entry, dict)),
            key=lambda entry: str(entry.get("started_at", "")),
        ),
        "remote": remote if isinstance(remote, dict) else {},
    }


def read_current_executions(path: Path) -> list[dict[str, Any]]:
    """Reader compatibility helper for callers that only need executions."""
    return list(read_runtime_status(path)["executions"])
