from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseConnection, DatabaseDsn
from server.app.db.migrations import (
    migrate_agent_catalog_cutover,
    migrate_code_executor_bindings,
    migrate_custom_node_codes,
    migrate_executor_asr_config_schema,
    migrate_executor_entity_type,
    migrate_external_connections,
    migrate_local_executor_removal,
    migrate_node_cms_config,
    migrate_versioned_entities,
    migrate_workspace_cms_config,
)
from server.app.db.migrations.job_status_counts import (
    migrate_workspace_job_status_counts,
)
from server.app.db.transaction import write_transaction

SCHEMA_VERSION = 41
_SCHEMA_FILE = Path(__file__).with_name("postgres_schema.sql")

# Vault (schema v16): idempotent DDL lives here because the architecture gate
# restricts schema mutations to this module (or a migrations/ package).
_WORKSPACE_SECRETS_DDL = """
create table if not exists workspace_secrets (
  workspace_id text not null references workspaces(id) on delete cascade,
  name text not null,
  ciphertext text not null,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  primary key(workspace_id, name)
)
"""


def migrate_workspace_secrets(conn: DatabaseConnection) -> None:
    """Create the workspace_secrets vault table (v16); idempotent on replay."""
    conn.execute(_WORKSPACE_SECRETS_DDL)


def init_db(database_dsn: DatabaseDsn) -> None:
    """Initialize or upgrade the PostgreSQL schema under a migration lock."""
    with write_transaction(database_dsn) as conn:
        # Serialize migrations per database, not cluster-wide: worktrees run
        # against dedicated databases (tests/postgres_support.py derives one
        # per worktree) and must not queue on each other's schema lock.
        conn.execute(
            "select pg_advisory_xact_lock(hashtext('agent-legion-schema-' || current_database()))"
        )
        conn.execute(
            """
            create table if not exists schema_migrations (
              version integer primary key,
              name text not null,
              applied_at timestamptz not null default current_timestamp
            )
            """
        )
        applied = conn.execute(
            "select version from schema_migrations where version = %s", (SCHEMA_VERSION,)
        ).fetchone()
        if applied is None:
            conn.execute(_SCHEMA_FILE.read_text(encoding="utf-8"))
            migrate_workspace_cms_config(conn)
            migrate_workspace_secrets(conn)
            migrate_code_executor_bindings(conn)
            migrate_local_executor_removal(conn)
            migrate_node_cms_config(conn)
            migrate_custom_node_codes(conn)
            migrate_versioned_entities(conn)
            migrate_agent_catalog_cutover(conn)
            migrate_executor_entity_type(conn)
            migrate_executor_asr_config_schema(conn)
            migrate_external_connections(conn)
            migrate_workspace_job_status_counts(conn)
            conn.execute("alter table workspaces drop column if exists cms_config_json")
            conn.execute(
                "insert into schema_migrations(version, name) values (%s, %s)",
                (SCHEMA_VERSION, "auth_scoped_tokens"),
            )
