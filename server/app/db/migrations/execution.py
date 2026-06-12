import sqlite3

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.models import Migration


def apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Run a single migration inside an explicit transaction and commit it."""
    conn.execute("BEGIN")
    try:
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
