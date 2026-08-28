from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

DatabaseDsn = str

# Connect-source contract (#187, BOUNDARY-DATA-001): production wiring hands
# the JobQueries facade to services and connection helpers alike; tests and
# CLI entry points keep the bare DSN string.
ConnectSource: TypeAlias = "JobQueries | DatabaseDsn"


def resolve_dsn(source: ConnectSource) -> DatabaseDsn:
    """Bare DSN of a facade-or-DSN source (#187); cache keys need the string."""
    return source if isinstance(source, str) else str(getattr(source, "dsn_identity", "") or "")


def postgres_sql(sql: str) -> str:
    """Validate that SQL carries no legacy SQLite-style ``?`` placeholders.

    The SQLite→PostgreSQL migration rewrote every placeholder to psycopg's
    ``%s`` (issue #17), retiring the blind-rewrite shim that would have
    corrupted Postgres JSON operators (``?`` / ``?|`` / ``?&``). This guard
    fails fast if a ``?`` slips back in through a path the static ratchet
    (``scripts/architecture/sql_placeholders.py``, the permanent first line
    of defense) cannot see, e.g. dynamically assembled SQL fragments.
    """
    if "?" in sql:
        raise ValueError("SQL contains legacy '?' placeholder; write psycopg '%s' directly")
    return sql
