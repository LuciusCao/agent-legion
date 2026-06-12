import sqlite3


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for ``table``.

    ``table`` must be a hard-coded identifier from migration code, never
    user input.
    """
    return {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Execute ``ddl`` when ``column`` is missing from ``table``.

    ``table`` and ``column`` are hard-coded identifiers; ``ddl`` is a complete
    ``ALTER TABLE ... ADD COLUMN ...`` statement.
    """
    if column not in column_names(conn, table):
        conn.execute(ddl)
