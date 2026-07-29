"""Data migrations applied alongside the idempotent DDL replay."""

from server.app.db.migrations.cms_config import migrate_workspace_cms_config
from server.app.db.migrations.code_executor import migrate_code_executor_bindings
from server.app.db.migrations.local_executor_removal import migrate_local_executor_removal

__all__ = [
    "migrate_code_executor_bindings",
    "migrate_local_executor_removal",
    "migrate_workspace_cms_config",
]
