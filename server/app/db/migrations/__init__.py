"""Data migrations applied alongside the idempotent DDL replay."""

from server.app.db.migrations.agent_catalog_cutover import migrate_agent_catalog_cutover
from server.app.db.migrations.cms_config import migrate_workspace_cms_config
from server.app.db.migrations.code_executor import migrate_code_executor_bindings
from server.app.db.migrations.custom_node_codes import migrate_custom_node_codes
from server.app.db.migrations.executor_asr_config_schema import (
    migrate_executor_asr_config_schema,
)
from server.app.db.migrations.executor_entity_type import migrate_executor_entity_type
from server.app.db.migrations.external_connections import migrate_external_connections
from server.app.db.migrations.hmac_connection_type import migrate_hmac_connection_type
from server.app.db.migrations.local_executor_removal import migrate_local_executor_removal
from server.app.db.migrations.node_cms_config import migrate_node_cms_config
from server.app.db.migrations.scoped_token_origin import migrate_scoped_token_origin
from server.app.db.migrations.studio_chat import migrate_studio_chat_tables
from server.app.db.migrations.studio_chat_context import migrate_studio_chat_context
from server.app.db.migrations.versioned_entities import migrate_versioned_entities

__all__ = [
    "migrate_agent_catalog_cutover",
    "migrate_code_executor_bindings",
    "migrate_custom_node_codes",
    "migrate_executor_asr_config_schema",
    "migrate_executor_entity_type",
    "migrate_external_connections",
    "migrate_hmac_connection_type",
    "migrate_local_executor_removal",
    "migrate_node_cms_config",
    "migrate_scoped_token_origin",
    "migrate_studio_chat_context",
    "migrate_studio_chat_tables",
    "migrate_versioned_entities",
    "migrate_workspace_cms_config",
]
