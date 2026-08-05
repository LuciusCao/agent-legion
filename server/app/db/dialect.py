from __future__ import annotations

DatabaseDsn = str


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
