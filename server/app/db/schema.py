from __future__ import annotations

from pathlib import Path

from server.app.db.bootstrap import bootstrap_default_workspace
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import write_transaction

SCHEMA_VERSION = 7
_SCHEMA_FILE = Path(__file__).with_name("postgres_schema.sql")


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
            conn.execute(
                "insert into schema_migrations(version, name) values (?, ?)",
                (SCHEMA_VERSION, "scoped_agent_register_tokens"),
            )
        bootstrap_default_workspace(conn)
