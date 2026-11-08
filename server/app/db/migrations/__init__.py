# Data migrations applied alongside the idempotent DDL replay.

from .agent_catalog_cutover import migrate_agent_catalog_cutover  # noqa: F401
from .agent_request_kind_window import migrate_agent_request_kind_window  # noqa: F401
from .agent_workspace_scope import migrate_agent_workspace_scope  # noqa: F401
from .cms_config import migrate_workspace_cms_config  # noqa: F401
from .code_executor import migrate_code_executor_bindings  # noqa: F401
from .custom_node_codes import migrate_custom_node_codes  # noqa: F401
from .execution_generation import migrate_execution_generation  # noqa: F401
from .executor_asr_config_schema import migrate_executor_asr_config_schema  # noqa: F401
from .executor_entity_type import migrate_executor_entity_type  # noqa: F401
from .executor_retirement import migrate_executor_retirement  # noqa: F401
from .external_connections import migrate_external_connections  # noqa: F401
from .hmac_connection_type import migrate_hmac_connection_type  # noqa: F401
from .job_artifacts import migrate_job_artifacts  # noqa: F401
from .job_node_status_counts import migrate_workspace_job_node_status_counts  # noqa: F401
from .job_status_counts_advisory_locks import migrate_job_status_counts_advisory_locks  # noqa: F401
from .job_status_counts_statement_triggers import (
    migrate_job_status_counts_statement_triggers,  # noqa: F401
)
from .jobs_run_id_index import migrate_jobs_run_id_index  # noqa: F401
from .local_executor_removal import migrate_local_executor_removal  # noqa: F401
from .node_cms_config import migrate_node_cms_config  # noqa: F401
from .ops_runtime_profile_samples import migrate_ops_runtime_profile_samples  # noqa: F401
from .retire_global_register_tokens import migrate_retire_global_register_tokens  # noqa: F401
from .run_job_status_counts import migrate_run_job_status_counts  # noqa: F401
from .runs import migrate_runs  # noqa: F401
from .scoped_token_origin import migrate_scoped_token_origin  # noqa: F401
from .studio_chat import migrate_studio_chat_tables  # noqa: F401
from .studio_chat_context import migrate_studio_chat_context  # noqa: F401
from .studio_chat_draft import migrate_studio_chat_draft  # noqa: F401
from .studio_publish_requests import migrate_studio_publish_requests  # noqa: F401
from .versioned_entities import migrate_versioned_entities  # noqa: F401
from .workflow_catalog_retirement import migrate_workflow_catalog_retirement  # noqa: F401
from .workflow_node_explicit_types import migrate_workflow_node_explicit_types  # noqa: F401
from .workspace_api_tokens import migrate_workspace_api_tokens  # noqa: F401
from .workspace_execution_defaults import migrate_workspace_execution_defaults  # noqa: F401
from .workspace_id_key_binding import migrate_workspace_id_key_binding  # noqa: F401
from .workspace_secrets import migrate_workspace_secrets  # noqa: F401

# The export list is derived from the imported migration functions themselves
# (one export per module, no hand-maintained duplicate that grows per version).
__all__ = [
    name
    for name, value in sorted(globals().items())
    if name.startswith("migrate_") and callable(value)
]
