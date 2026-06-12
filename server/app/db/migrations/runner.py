import logging
import sqlite3

from server.app.db.migrations.execution import apply_migration
from server.app.db.migrations.history import check_history, load_applied
from server.app.db.migrations.models import Migration
from server.app.db.migrations.registry_validation import ensure_foreign_keys, validate_registry

logger = logging.getLogger(__name__)


def run_migrations(
    conn: sqlite3.Connection,
    migrations: tuple[Migration, ...] | list[Migration] | None = None,
) -> None:
    """Run pending migrations in ascending order, owning one transaction each."""
    migration_tuple: tuple[Migration, ...] = tuple(migrations or ())
    registry = validate_registry(migration_tuple)
    sorted_migrations = tuple(sorted(migration_tuple, key=lambda m: m.version))
    try:
        ensure_foreign_keys(conn)
        applied = load_applied(conn)
        check_history(applied, registry)
        for migration in sorted_migrations:
            if migration.version in applied:
                continue
            logger.info("Applying migration %d: %s", migration.version, migration.name)
            apply_migration(conn, migration)
    finally:
        ensure_foreign_keys(conn)
