from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import no_result


class DatabaseRow(dict[str, Any]):
    """Mapping row with SQLite-compatible positional lookup during migration."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        normalized = [_row_value(value) for value in values]
        super().__init__(zip(names, normalized, strict=True))
        self._values = normalized

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_value(value: Any) -> Any:
    """Render datetimes as ISO-8601 UTC strings with an explicit offset.

    The ``+00:00`` suffix keeps the timezone on the wire: browsers parse
    offset-less strings as local time, which shifted every timestamp by the
    host offset (e.g. +8h in CST).
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def string_dict_row(cursor: Any):
    if cursor.description is None:
        return no_result
    names = [column.name for column in cursor.description]

    def make_row(values: Sequence[Any]) -> dict[str, Any]:
        return DatabaseRow(names, values)

    return make_row


def configure_connection(conn: Connection[dict[str, Any]]) -> None:
    """Make naive legacy timestamp strings deterministic across host timezones."""
    conn.execute("set timezone = 'UTC'")
    conn.commit()
