from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.storage_layout import job_storage_dir
from server.app.services.run_payload import candidate_input
from server.app.storage_paths import make_data_relative


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


_MATERIAL_LOCK_SQL = "select id from materials where id=%s and workspace_id=%s for key share"
_BUNDLE_LOCK_SQL = "select id from material_bundles where id=%s and workspace_id=%s for key share"


class JobBulkQueriesMixin(ConnectionQueriesMixin):
    jobs_dir: Path

    def create_jobs_bulk(
        self,
        *,
        candidates: list[dict[str, Any]],
        workflow_key: str,
        run_id: str,
        node_keys: list[str],
        workspace_id: str,
        revision: dict[str, Any],
        frozen_config: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Insert one job per candidate of a run, freezing config + input.

        Every job carries the run's frozen node config (``frozen_config_json``)
        and its own input document (``input_json``, RUN-FREEZE-001); a
        re-submitted job takes the new freeze, matching the old semantics
        where re-pointing ``batch_id`` re-bound the batch payload.
        """
        if not candidates:
            return []
        frozen_config_json = (
            json.dumps(dict(frozen_config), ensure_ascii=False, sort_keys=True)
            if frozen_config
            else None
        )
        rows: list[tuple[Any, ...]] = []
        job_ids: list[str] = []
        identities: dict[str, tuple[str, str]] = {}
        storage_dirs: dict[str, Path] = {}
        material_ids: list[str] = []
        bundle_ids: list[str] = []
        for candidate in candidates:
            source_id = str(candidate["entity_id"])
            job_id = _job_id(workspace_id, workflow_key, source_id)
            identity = (str(candidate["entity_type"]), source_id)
            if job_id in identities and identities[job_id] != identity:
                raise ValueError(f"Job identity collision for {job_id}")
            identities[job_id] = identity
            # The dir is created only after the existing-row check below: for a
            # resubmitted job the on-conflict update keeps the stored
            # storage_dir, so pre-creating here would leave a stray shard dir
            # that blocks the one-shot flat→sharded migration as a conflict.
            storage_dir = job_storage_dir(self.jobs_dir, workspace_id, job_id)
            storage_dirs[job_id] = storage_dir
            job_ids.append(job_id)
            input_doc = candidate_input(candidate)
            if input_doc.get("type") == "material":
                material_ids.append(str(input_doc.get("material_id") or ""))
            elif input_doc.get("type") == "bundle":
                bundle_ids.append(str(input_doc.get("bundle_id") or ""))
            rows.append(
                (
                    job_id,
                    workspace_id,
                    workflow_key,
                    str(candidate["entity_type"]),
                    source_id,
                    run_id,
                    str(candidate["title"]),
                    make_data_relative(storage_dir, self.jobs_dir.parent),
                    str(candidate.get("stem", "")),
                    revision["id"],
                    int(revision["version"]),
                    revision["definition_hash"],
                    revision["definition_json"],
                    json.dumps(input_doc, ensure_ascii=False),
                    frozen_config_json,
                )
            )

        with self.connect() as conn:
            # Material inputs FOR KEY SHARE their materials row so a concurrent
            # material delete (FOR UPDATE, materials service) serializes with
            # this insert: the delete either blocks until these jobs commit
            # (its reference check then rejects it with 409) or commits first
            # and the row is gone here. A missing row fails the whole run
            # creation (run service maps ValueError to 400 + compensation).
            for material_id in dict.fromkeys(material_ids):
                locked = conn.execute(_MATERIAL_LOCK_SQL, (material_id, workspace_id)).fetchone()
                if locked is None:
                    raise ValueError(f"Material not found: {material_id}")
            # Bundle inputs FOR KEY SHARE their bundle row for the same
            # serialization against the bundle delete guard (#156).
            for bundle_id in dict.fromkeys(bundle_ids):
                locked = conn.execute(_BUNDLE_LOCK_SQL, (bundle_id, workspace_id)).fetchone()
                if locked is None:
                    raise ValueError(f"Material bundle not found: {bundle_id}")
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
            for row in rows:
                job_id = str(row[0])
                if job_id not in by_id:
                    storage_dirs[job_id].mkdir(parents=True, exist_ok=True)
            conn.executemany(
                """
                insert into jobs(
                  id, workspace_id, workflow_key, source_type, source_id, run_id, title,
                  storage_dir, stem, workflow_revision_id, workflow_version,
                  workflow_definition_hash, workflow_definition_snapshot_json,
                  input_json, frozen_config_json
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict(id) do update set
                  title=excluded.title, stem=excluded.stem, run_id=excluded.run_id,
                  input_json=excluded.input_json, frozen_config_json=excluded.frozen_config_json,
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
