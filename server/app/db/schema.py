from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseConnection, DatabaseDsn
from server.app.db.migrations import migrate_workspace_cms_config
from server.app.db.transaction import write_transaction

SCHEMA_VERSION = 18
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
        conn.execute("select pg_advisory_xact_lock(hashtext('agent-legion-schema'))")
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
            "select version from schema_migrations where version = ?", (SCHEMA_VERSION,)
        ).fetchone()
        if applied is None:
            conn.execute(_SCHEMA_FILE.read_text(encoding="utf-8"))
            migrate_workspace_cms_config(conn)
            migrate_workspace_secrets(conn)
            conn.execute("alter table workspaces drop column if exists cms_config_json")
            conn.execute(
                "insert into schema_migrations(version, name) values (?, ?)",
                (SCHEMA_VERSION, "agent_claim_queue_index"),
            )
