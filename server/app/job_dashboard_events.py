from __future__ import annotations

import json
from typing import Any


def build_workspace_stats_batch_payload(
    latest_revision: int,
    workspaces: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "type": "workspace_stats_batch",
            "latest_revision": latest_revision,
            "workspaces": workspaces,
        }
    )


def broadcast_workspace_stats_batch(
    job_event_manager: Any,
    latest_revision: int,
    workspace_stats: list[dict[str, Any]],
) -> None:
    if not workspace_stats:
        return
    job_event_manager._broadcast(
        "__dashboard__",
        build_workspace_stats_batch_payload(latest_revision, workspace_stats),
    )
