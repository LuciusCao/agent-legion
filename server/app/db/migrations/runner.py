import logging
import sqlite3
from collections.abc import Callable

from server.app.db.migrations.execution import apply_migration
from server.app.db.migrations.history import check_history, load_applied
from server.app.db.migrations.hooks import (
    _call_phase_hook,
    _reset_phase_hook,
    _set_phase_hook,
)
from server.app.db.migrations.models import Migration
from server.app.db.migrations.registry_validation import ensure_foreign_keys, validate_registry

logger = logging.getLogger(__name__)


def run_migrations(
    conn: sqlite3.Connection,
    migrations: tuple[Migration, ...] | list[Migration] | None = None,
    *,
    _phase_hook: Callable[[str], None] | None = None,
) -> None:
    """Run pending migrations; ``_phase_hook`` is internal test-only failure injection."""
    migration_tuple: tuple[Migration, ...] = tuple(migrations or ())
    registry = validate_registry(migration_tuple)
    sorted_migrations = tuple(sorted(migration_tuple, key=lambda m: m.version))
    token = _set_phase_hook(_phase_hook)
    try:
        ensure_foreign_keys(conn)
        applied = load_applied(conn)
        check_history(applied, registry)
        for migration in sorted_migrations:
            if migration.version in applied:
                continue
            logger.info("Applying migration %d: %s", migration.version, migration.name)
            _call_phase_hook(f"pre:{migration.name}")
            apply_migration(conn, migration)
            _call_phase_hook(f"post:{migration.name}")
    finally:
        _reset_phase_hook(token)
        ensure_foreign_keys(conn)
