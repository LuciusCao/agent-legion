import sqlite3

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.models import Migration


def apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Run a single migration inside a SQLite-managed implicit transaction."""
    with conn:
        # SQLite only opens an implicit transaction on the first DML statement.
        # Ensure one is active before any DDL so schema changes roll back on failure.
        conn.execute("update schema_migrations set version = version where 0=1")
        migration.apply(conn)
        if migration.rebuilds_fk:
            violations = conn.execute("pragma foreign_key_check").fetchall()
            if violations:
                details = "; ".join(str(row) for row in violations)
                raise MigrationError(
                    f"foreign key check failed for migration {migration.version}: {details}"
                )
        conn.execute(
            "insert into schema_migrations(version, name) values (?, ?)",
            (migration.version, migration.name),
        )
