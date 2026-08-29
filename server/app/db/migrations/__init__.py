# Data migrations applied alongside the idempotent DDL replay.

from server.app.db.migrations.agent_catalog_cutover import (
    migrate_agent_catalog_cutover,  # noqa: F401
)
from server.app.db.migrations.agent_request_kind_window import (
    migrate_agent_request_kind_window,  # noqa: F401
)
from server.app.db.migrations.agent_workspace_scope import (
    migrate_agent_workspace_scope,  # noqa: F401
)
from server.app.db.migrations.cms_config import migrate_workspace_cms_config  # noqa: F401
from server.app.db.migrations.code_executor import migrate_code_executor_bindings  # noqa: F401
from server.app.db.migrations.custom_node_codes import migrate_custom_node_codes  # noqa: F401
from server.app.db.migrations.executor_asr_config_schema import (  # noqa: F401
    migrate_executor_asr_config_schema,
)
from server.app.db.migrations.executor_entity_type import migrate_executor_entity_type  # noqa: F401
from server.app.db.migrations.executor_retirement import migrate_executor_retirement  # noqa: F401
from server.app.db.migrations.external_connections import migrate_external_connections  # noqa: F401
from server.app.db.migrations.hmac_connection_type import migrate_hmac_connection_type  # noqa: F401
from server.app.db.migrations.job_artifacts import migrate_job_artifacts  # noqa: F401
from server.app.db.migrations.job_node_status_counts import (  # noqa: F401
    migrate_workspace_job_node_status_counts,
)
from server.app.db.migrations.jobs_run_id_index import migrate_jobs_run_id_index  # noqa: F401
from server.app.db.migrations.local_executor_removal import (
    migrate_local_executor_removal,  # noqa: F401
)
from server.app.db.migrations.node_cms_config import migrate_node_cms_config  # noqa: F401
from server.app.db.migrations.retire_global_register_tokens import (  # noqa: F401
    migrate_retire_global_register_tokens,
)
from server.app.db.migrations.runs import migrate_runs  # noqa: F401
from server.app.db.migrations.scoped_token_origin import migrate_scoped_token_origin  # noqa: F401
from server.app.db.migrations.studio_chat import migrate_studio_chat_tables  # noqa: F401
from server.app.db.migrations.studio_chat_context import migrate_studio_chat_context  # noqa: F401
from server.app.db.migrations.studio_chat_draft import migrate_studio_chat_draft  # noqa: F401
from server.app.db.migrations.versioned_entities import migrate_versioned_entities  # noqa: F401
from server.app.db.migrations.workflow_catalog_retirement import (  # noqa: F401
    migrate_workflow_catalog_retirement,
)
from server.app.db.migrations.workspace_execution_defaults import (  # noqa: F401
    migrate_workspace_execution_defaults,
)
from server.app.db.migrations.workspace_id_key_binding import (  # noqa: F401
    migrate_workspace_id_key_binding,
)
from server.app.db.migrations.workspace_secrets import migrate_workspace_secrets  # noqa: F401

# The export list is derived from the imported migration functions themselves
# (one export per module, no hand-maintained duplicate that grows per version).
__all__ = [
    name
    for name, value in sorted(globals().items())
    if name.startswith("migrate_") and callable(value)
]
