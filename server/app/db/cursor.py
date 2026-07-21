from __future__ import annotations

from typing import Any, cast


class Cursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._cursor.fetchone())

    def fetchall(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._cursor.fetchall())

    def __iter__(self):
        return iter(self._cursor)
