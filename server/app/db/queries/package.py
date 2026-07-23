from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from server.app.db.queries.base import VideoQueriesBase


class PackageQueriesMixin(VideoQueriesBase):
    def insert_package(
        self,
        path: str,
        name: str = "",
        video_count: int = 0,
        size_bytes: int = 0,
        locked: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "insert into packages(path, name, video_count, size_bytes, locked, created_at) values (?, ?, ?, ?, ?, ?)",
                (path, name, video_count, size_bytes, locked, datetime.now(UTC).isoformat()),
            )

    def list_packages(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            return [
                cast(dict[str, Any], dict(row))
                for row in conn.execute(
                    "select * from packages order by created_at desc limit ?",
                    (limit,),
                )
            ]

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from packages where id = ?",
                (package_id,),
            ).fetchone()
            return cast(dict[str, Any], dict(row)) if row is not None else None

    def delete_package(self, package_id: int) -> None:
        with self.connect() as conn:
            conn.execute("delete from packages where id = ?", (package_id,))

    def update_package_name(self, package_id: int, name: str) -> None:
        with self.connect() as conn:
            conn.execute("update packages set name = ? where id = ?", (name, package_id))

    def update_package_stats(
        self,
        package_id: int,
        *,
        name: str | None = None,
        video_count: int | None = None,
        size_bytes: int | None = None,
        locked: int | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if video_count is not None:
            fields.append("video_count = ?")
            values.append(video_count)
        if size_bytes is not None:
            fields.append("size_bytes = ?")
            values.append(size_bytes)
        if locked is not None:
            fields.append("locked = ?")
            values.append(locked)
        if not fields:
            return
        sql = f"update packages set {', '.join(fields)} where id = ?"
        with self.connect() as conn:
            conn.execute(sql, values + [package_id])
