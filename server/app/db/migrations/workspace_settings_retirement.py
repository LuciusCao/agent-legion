"""Schema v64 post-chain cleanup: drop retired workspace columns.

Workspace-level Agent defaults are retired at v64 — execution config resolves
from the node / workflow top-level execution block only; the v64 data migration
(migrations/workspace_execution_defaults.py) backfills them into the active
revision first. ``intake_config_json`` retires alongside. The schema file still
CREATEs the columns so the v62 data migration can replay its insert on older
databases, so the drop trails the chain (the cms_config_json pattern).
"""

from __future__ import annotations

from typing import Any

_RETIRED_WORKSPACE_COLUMNS = (
    "default_agent_provider",
    "default_agent_model",
    "default_agent_thinking",
    "intake_config_json",
)


def drop_retired_workspace_setting_columns(conn: Any) -> None:
    # Idempotent; called from schema.py after the migration loop.
    for retired_column in _RETIRED_WORKSPACE_COLUMNS:
        conn.execute(f"alter table workspaces drop column if exists {retired_column}")
