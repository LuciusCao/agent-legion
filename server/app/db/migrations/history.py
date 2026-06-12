import sqlite3

from server.app.db.migrations.errors import MigrationHistoryError
from server.app.db.migrations.models import Migration


def load_applied(conn: sqlite3.Connection) -> dict[int, str]:
    """Create the migration history table and return applied version -> name."""
    conn.execute(
        """
        create table if not exists schema_migrations (
          version integer primary key,
          name text not null,
          applied_at text not null default current_timestamp
        )
        """
    )
    return {
        row["version"]: row["name"]
        for row in conn.execute("select version, name from schema_migrations").fetchall()
    }


def check_history(applied: dict[int, str], registry: dict[int, Migration]) -> None:
    """Ensure recorded history matches the registered migrations."""
    for version, name in applied.items():
        migration = registry.get(version)
        if migration is None:
            raise MigrationHistoryError(
                f"schema_migrations contains version {version} but no migration "
                "is registered with that version"
            )
        if migration.name != name:
            raise MigrationHistoryError(
                f"schema_migrations version {version} is recorded as {name!r}, "
                f"expected {migration.name!r}"
            )
