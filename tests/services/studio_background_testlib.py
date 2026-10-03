"""Temporary Kimi V1 task-store fixtures shared by lifecycle tests."""

import json


def write_task(root, task_id="agent-1", status="running", **spec_overrides):
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    spec = {
        "version": 1,
        "id": task_id,
        "session_id": "acp-1",
        "kind": "agent",
        "owner_role": "root",
        **spec_overrides,
    }
    (path / "spec.json").write_text(json.dumps(spec))
    (path / "runtime.json").write_text(json.dumps({"status": status}))
    return path
