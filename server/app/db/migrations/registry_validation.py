import sqlite3

from server.app.db.migrations.errors import MigrationRegistryError
from server.app.db.migrations.models import Migration


def validate_registry(migrations: tuple[Migration, ...]) -> dict[int, Migration]:
    """Validate the registry and return a version-indexed mapping."""
    seen_versions: set[int] = set()
    duplicates: set[int] = set()
    for migration in migrations:
        if migration.version in seen_versions:
            duplicates.add(migration.version)
        seen_versions.add(migration.version)
    if duplicates:
        raise MigrationRegistryError(f"duplicate migration versions: {sorted(duplicates)}")
    if any(migration.version <= 0 for migration in migrations):
        raise MigrationRegistryError("migration versions must be positive integers")
    return {migration.version: migration for migration in migrations}


def ensure_foreign_keys(conn: sqlite3.Connection) -> None:
    """Turn foreign-key enforcement on for the connection."""
    conn.execute("pragma foreign_keys=ON")
