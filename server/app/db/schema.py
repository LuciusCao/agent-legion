from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.db.migrations import (
    migrate_agent_catalog_cutover,
    migrate_agent_request_kind_window,
    migrate_agent_workspace_scope,
    migrate_code_executor_bindings,
    migrate_custom_node_codes,
    migrate_executor_asr_config_schema,
    migrate_executor_entity_type,
    migrate_executor_retirement,
    migrate_external_connections,
    migrate_hmac_connection_type,
    migrate_local_executor_removal,
    migrate_node_cms_config,
    migrate_scoped_token_origin,
    migrate_studio_chat_context,
    migrate_studio_chat_tables,
    migrate_versioned_entities,
    migrate_workflow_catalog_retirement,
    migrate_workspace_cms_config,
    migrate_workspace_secrets,
)
from server.app.db.migrations.job_status_counts import (
    migrate_workspace_job_status_counts,
)
from server.app.db.transaction import write_transaction

SCHEMA_VERSION = 51
_SCHEMA_FILE = Path(__file__).with_name("postgres_schema.sql")


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
            migrate_hmac_connection_type(conn)
            migrate_workspace_job_status_counts(conn)
            migrate_scoped_token_origin(conn)
            migrate_studio_chat_tables(conn)
            migrate_studio_chat_context(conn)
            migrate_agent_workspace_scope(conn)
            migrate_executor_retirement(conn)
            migrate_workflow_catalog_retirement(conn)
            migrate_agent_request_kind_window(conn)
            conn.execute("alter table workspaces drop column if exists cms_config_json")
            conn.execute(
                "insert into schema_migrations(version, name) values (%s, %s)",
                (SCHEMA_VERSION, "agent_request_kind_window"),
            )
