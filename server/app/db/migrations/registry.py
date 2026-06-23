from server.app.db.migrations.models import Migration
from server.app.db.migrations.v001_executor_core import MIGRATION as V001_EXECUTOR_CORE
from server.app.db.migrations.v002_executor_bootstrap_state import (
    MIGRATION as V002_EXECUTOR_BOOTSTRAP_STATE,
)
from server.app.db.migrations.v003_legacy_columns import MIGRATION as V003_LEGACY_COLUMNS
from server.app.db.migrations.v004_workspace_dag_foreign_keys import (
    MIGRATION as V004_WORKSPACE_DAG_FOREIGN_KEYS,
)
from server.app.db.migrations.v006_job_execution_control import (
    MIGRATION as V006_JOB_EXECUTION_CONTROL,
)
from server.app.db.migrations.v007_rename_pipeline_to_workflow import (
    MIGRATION as V007_RENAME_PIPELINE_TO_WORKFLOW,
)
from server.app.db.migrations.v008_job_node_created_at import (
    MIGRATION as V008_JOB_NODE_CREATED_AT,
)
from server.app.db.migrations.v009_relative_path_storage import (
    MIGRATION as V009_RELATIVE_PATH_STORAGE,
)
from server.app.db.migrations.v010_remove_default_workspace import (
    MIGRATION as V010_REMOVE_DEFAULT_WORKSPACE,
)
from server.app.db.migrations.v011_remove_workspace_id_defaults import (
    MIGRATION as V011_REMOVE_WORKSPACE_ID_DEFAULTS,
)
from server.app.db.migrations.v012_workspace_packages import (
    MIGRATION as V012_WORKSPACE_PACKAGES,
)

MIGRATIONS: tuple[Migration, ...] = (
    V001_EXECUTOR_CORE,
    V002_EXECUTOR_BOOTSTRAP_STATE,
    V003_LEGACY_COLUMNS,
    V004_WORKSPACE_DAG_FOREIGN_KEYS,
    V006_JOB_EXECUTION_CONTROL,
    V007_RENAME_PIPELINE_TO_WORKFLOW,
    V008_JOB_NODE_CREATED_AT,
    V009_RELATIVE_PATH_STORAGE,
    V010_REMOVE_DEFAULT_WORKSPACE,
    V011_REMOVE_WORKSPACE_ID_DEFAULTS,
    V012_WORKSPACE_PACKAGES,
)
