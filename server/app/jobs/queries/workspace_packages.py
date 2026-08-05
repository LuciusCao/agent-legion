from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from server.app.jobs.queries.base import JobQueriesBase


class WorkspacePackageQueriesMixin(JobQueriesBase):
    def insert_workspace_package(
        self,
        workspace_id: str,
        path: str,
        name: str = "",
        job_count: int = 0,
        size_bytes: int = 0,
        locked: int = 0,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                insert into workspace_packages(
                  workspace_id, path, name, job_count, size_bytes, locked, created_at
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    workspace_id,
                    path,
                    name,
                    job_count,
                    size_bytes,
                    locked,
                    datetime.now(UTC).isoformat(),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("workspace package insert did not return an id")
            return int(row["id"])

    def list_workspace_packages(self, workspace_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workspace_packages
                where workspace_id = %s
                order by created_at desc
                limit %s
                """,
                (workspace_id, limit),
            )
            return [cast(dict[str, Any], dict(row)) for row in rows]

    def get_workspace_package(self, workspace_id: str, package_id: int) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workspace_packages
                where workspace_id = %s and id = %s
                """,
                (workspace_id, package_id),
            ).fetchone()
            return dict(row) if row else None

    def update_workspace_package_name(self, workspace_id: str, package_id: int, name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update workspace_packages set name = %s
                where workspace_id = %s and id = %s
                """,
                (name, workspace_id, package_id),
            )

    def update_workspace_package_locked(
        self, workspace_id: str, package_id: int, locked: int
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update workspace_packages set locked = %s
                where workspace_id = %s and id = %s
                """,
                (locked, workspace_id, package_id),
            )

    def delete_workspace_package(self, workspace_id: str, package_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                delete from workspace_packages
                where workspace_id = %s and id = %s
                """,
                (workspace_id, package_id),
            )

    def set_jobs_packed(self, job_ids: list[str], packed: int) -> None:
        if not job_ids:
            return
        placeholders = ",".join("%s" for _ in job_ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                update jobs set packed = %s where id in ({placeholders})
                """,
                (packed, *job_ids),
            )
