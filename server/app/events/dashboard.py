from __future__ import annotations

import json
from typing import Any

from server.app.events.bus import EventBus


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
    bus: EventBus,
    latest_revision: int,
    workspace_stats: list[dict[str, Any]],
) -> None:
    if not workspace_stats:
        return
    bus.publish(
        "dashboard",
        build_workspace_stats_batch_payload(latest_revision, workspace_stats),
    )
