import logging
import sqlite3
from collections.abc import Callable

logger = logging.getLogger(__name__)

MigrationApply = Callable[[sqlite3.Connection], None]


class Migration:
    def __init__(self, version: int, name: str, apply: MigrationApply) -> None:
        self.version = version
        self.name = name
        self.apply = apply


def run_migrations(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> None:
    """Run pending migrations in ascending order inside the caller's transaction."""
    conn.execute(
        """
        create table if not exists schema_migrations (
          version integer primary key,
          name text not null,
          applied_at text not null default current_timestamp
        )
        """
    )

    if not conn.in_transaction:
        conn.execute("BEGIN")

    applied = {
        row["version"] for row in conn.execute("select version from schema_migrations").fetchall()
    }

    for migration in migrations or []:
        if migration.version in applied:
            continue
        logger.info("Applying migration %d: %s", migration.version, migration.name)
        migration.apply(conn)
        conn.execute(
            "insert into schema_migrations(version, name) values (?, ?)",
            (migration.version, migration.name),
        )
        applied.add(migration.version)
