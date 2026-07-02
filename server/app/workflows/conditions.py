from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.workflows.definition import WorkflowCondition, WorkflowEdge


def _read_path(payload: Any, path: str) -> Any:
    if path == "$":
        return payload
    if not path.startswith("$."):
        return None
    current = payload
    for part in path[2:].split("."):
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def condition_matches(condition: WorkflowCondition, artifact_dir: Path) -> bool:
    path = artifact_dir / condition.artifact
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(_read_path(payload, condition.path) == condition.equals)


def selected_edges(edges: list[WorkflowEdge], artifact_dir: Path) -> list[WorkflowEdge]:
    selected: list[WorkflowEdge] = []
    for edge in edges:
        if edge.condition is None or condition_matches(edge.condition, artifact_dir):
            selected.append(edge)
    return selected
