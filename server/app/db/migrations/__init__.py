# Data migrations applied alongside the idempotent DDL replay.

from server.app.db.migrations.agent_catalog_cutover import migrate_agent_catalog_cutover
from server.app.db.migrations.agent_request_kind_window import migrate_agent_request_kind_window
from server.app.db.migrations.agent_workspace_scope import migrate_agent_workspace_scope
from server.app.db.migrations.cms_config import migrate_workspace_cms_config
from server.app.db.migrations.code_executor import migrate_code_executor_bindings
from server.app.db.migrations.custom_node_codes import migrate_custom_node_codes
from server.app.db.migrations.executor_asr_config_schema import (
    migrate_executor_asr_config_schema,
)
from server.app.db.migrations.executor_entity_type import migrate_executor_entity_type
from server.app.db.migrations.executor_retirement import migrate_executor_retirement
from server.app.db.migrations.external_connections import migrate_external_connections
from server.app.db.migrations.hmac_connection_type import migrate_hmac_connection_type
from server.app.db.migrations.job_artifacts import migrate_job_artifacts
from server.app.db.migrations.job_node_status_counts import (
    migrate_workspace_job_node_status_counts,
)
from server.app.db.migrations.local_executor_removal import migrate_local_executor_removal
from server.app.db.migrations.node_cms_config import migrate_node_cms_config
from server.app.db.migrations.node_secret_sweep import migrate_node_secret_sweep
from server.app.db.migrations.runs import migrate_runs
from server.app.db.migrations.scoped_token_origin import migrate_scoped_token_origin
from server.app.db.migrations.studio_chat import migrate_studio_chat_tables
from server.app.db.migrations.studio_chat_context import migrate_studio_chat_context
from server.app.db.migrations.versioned_entities import migrate_versioned_entities
from server.app.db.migrations.workflow_catalog_retirement import (
    migrate_workflow_catalog_retirement,
)
from server.app.db.migrations.workspace_secrets import migrate_workspace_secrets

__all__ = [
    "migrate_agent_catalog_cutover",
    "migrate_agent_request_kind_window",
    "migrate_agent_workspace_scope",
    "migrate_code_executor_bindings",
    "migrate_custom_node_codes",
    "migrate_executor_asr_config_schema",
    "migrate_executor_entity_type",
    "migrate_executor_retirement",
    "migrate_external_connections",
    "migrate_hmac_connection_type",
    "migrate_job_artifacts",
    "migrate_local_executor_removal",
    "migrate_node_cms_config",
    "migrate_node_secret_sweep",
    "migrate_runs",
    "migrate_scoped_token_origin",
    "migrate_studio_chat_context",
    "migrate_studio_chat_tables",
    "migrate_versioned_entities",
    "migrate_workflow_catalog_retirement",
    "migrate_workspace_cms_config",
    "migrate_workspace_job_node_status_counts",
    "migrate_workspace_secrets",
]
