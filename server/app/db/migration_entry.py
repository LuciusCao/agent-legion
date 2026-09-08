"""The versioned migration entry record (split from ``migration_chain.py``).

The chain file pays one import + one registration line per schema version,
so its budget headroom is permanently scarce; the entry type itself is
shared vocabulary (``migration_registry.py`` re-exports it) and lives here
cycle-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaMigration:
    """One versioned entry (the newest migration introduced at this version)."""

    version: int
    name: str
    # Callable[[conn], None] | None, typed as Any so the typing imports
    # stay off the consumer files' budgets (every entry passes a plain
    # function).
    apply: Any = None
