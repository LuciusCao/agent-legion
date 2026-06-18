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

MIGRATIONS: tuple[Migration, ...] = (
    V001_EXECUTOR_CORE,
    V002_EXECUTOR_BOOTSTRAP_STATE,
    V003_LEGACY_COLUMNS,
    V004_WORKSPACE_DAG_FOREIGN_KEYS,
    V006_JOB_EXECUTION_CONTROL,
    V007_RENAME_PIPELINE_TO_WORKFLOW,
)
