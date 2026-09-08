"""Versioned schema migration registry: the public surface.

The chain itself lives in ``migration_chain.py`` (split when the import
list outgrew this file's budget) and the entry record in
``migration_entry.py`` (#551); this module re-exports both so existing
consumers (``schema.py``, the tests/db pin tests) keep their import path.
"""

from server.app.db.migration_chain import MIGRATIONS
from server.app.db.migration_entry import SchemaMigration

__all__ = ["MIGRATIONS", "SchemaMigration"]
