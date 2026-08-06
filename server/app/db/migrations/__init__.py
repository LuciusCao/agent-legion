"""Data migrations applied alongside the idempotent DDL replay."""

from server.app.db.migrations.agent_catalog_cutover import migrate_agent_catalog_cutover
from server.app.db.migrations.cms_config import migrate_workspace_cms_config
from server.app.db.migrations.code_executor import migrate_code_executor_bindings
from server.app.db.migrations.custom_node_codes import migrate_custom_node_codes
from server.app.db.migrations.local_executor_removal import migrate_local_executor_removal
from server.app.db.migrations.node_cms_config import migrate_node_cms_config
from server.app.db.migrations.versioned_entities import migrate_versioned_entities

__all__ = [
    "migrate_agent_catalog_cutover",
    "migrate_code_executor_bindings",
    "migrate_custom_node_codes",
    "migrate_local_executor_removal",
    "migrate_node_cms_config",
    "migrate_versioned_entities",
    "migrate_workspace_cms_config",
]
