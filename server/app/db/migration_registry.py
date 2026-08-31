"""Versioned schema migration registry: the public surface.

The chain itself lives in ``migration_chain.py`` (split when the import
list outgrew this file's budget); this module re-exports it so existing
consumers (``schema.py``, the tests/db pin tests) keep their import path.
"""

from server.app.db.migration_chain import MIGRATIONS, MigrationFn, SchemaMigration

__all__ = ["MIGRATIONS", "MigrationFn", "SchemaMigration"]
