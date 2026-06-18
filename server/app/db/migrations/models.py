import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Migration:
    """Versioned schema change.

    ``apply`` receives a connection that is inside an active transaction.
    It must not call ``commit()`` itself; the runner commits after the
    migration succeeds and after any requested foreign-key checks pass.
    """

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None] = field(compare=False)
    rebuilds_fk: bool = False
    backup_label: str | None = None
    backup_when: Callable[[sqlite3.Connection], bool] | None = field(
        default=None,
        compare=False,
    )
