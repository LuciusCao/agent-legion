from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.storage_layout import job_storage_dir
from server.app.storage_paths import make_data_relative


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


class JobBulkQueriesMixin(JobQueriesBase):
    jobs_dir: Path

    def create_jobs_bulk(
        self,
        *,
        candidates: list[dict[str, Any]],
        workflow_key: str,
        batch_id: str,
        node_keys: list[str],
        workspace_id: str,
        revision: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        rows: list[tuple[Any, ...]] = []
        job_ids: list[str] = []
        identities: dict[str, tuple[str, str]] = {}
        for candidate in candidates:
            source_id = str(candidate["entity_id"])
            job_id = _job_id(workspace_id, workflow_key, source_id)
            identity = (str(candidate["entity_type"]), source_id)
            if job_id in identities and identities[job_id] != identity:
                raise ValueError(f"Job identity collision for {job_id}")
            identities[job_id] = identity
            storage_dir = job_storage_dir(self.jobs_dir, workspace_id, job_id)
            storage_dir.mkdir(parents=True, exist_ok=True)
            job_ids.append(job_id)
            rows.append(
                (
                    job_id,
                    workspace_id,
                    workflow_key,
                    str(candidate["entity_type"]),
                    source_id,
                    batch_id,
                    str(candidate["title"]),
                    make_data_relative(storage_dir, self.jobs_dir.parent),
                    str(candidate.get("stem", "")),
                    revision["id"],
                    int(revision["version"]),
                    revision["definition_hash"],
                    revision["definition_json"],
                )
            )

        with self.connect() as conn:
            placeholders = ",".join("%s" for _ in job_ids)
            existing = conn.execute(
                f"select * from jobs where id in ({placeholders})", job_ids
            ).fetchall()
            by_id = {str(row["id"]): row for row in existing}
            for row in rows:
                current = by_id.get(str(row[0]))
                if current is not None and (
                    current["workspace_id"] != row[1]
                    or current["workflow_key"] != row[2]
                    or current["source_type"] != row[3]
                    or current["source_id"] != row[4]
                ):
                    raise ValueError(f"Job identity collision for {row[0]}")
            conn.executemany(
                """
                insert into jobs(
                  id, workspace_id, workflow_key, source_type, source_id, batch_id, title,
                  storage_dir, stem, workflow_revision_id, workflow_version,
                  workflow_definition_hash, workflow_definition_snapshot_json
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict(id) do update set
                  title=excluded.title, stem=excluded.stem, batch_id=excluded.batch_id,
                  updated_at=current_timestamp
                """,
                rows,
            )
            conn.executemany(
                """
                insert into job_nodes(job_id, node_key, status, created_at)
                values (%s, %s, 'pending', current_timestamp)
                on conflict(job_id, node_key) do nothing
                """,
                [(job_id, node_key) for job_id in job_ids for node_key in node_keys],
            )
            created = conn.execute(
                f"select * from jobs where id in ({placeholders})", job_ids
            ).fetchall()
        created_by_id = {str(row["id"]): dict(row) for row in created}
        return [created_by_id[job_id] for job_id in job_ids]
