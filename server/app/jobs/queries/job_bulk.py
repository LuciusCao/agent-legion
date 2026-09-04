from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.run_freeze import candidate_input
from server.app.jobs.storage_layout import job_storage_dir
from server.app.storage_paths import make_data_relative


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


_MATERIAL_LOCK_SQL = "select id from materials where id=%s and workspace_id=%s for key share"
_BUNDLE_LOCK_SQL = "select id from material_bundles where id=%s and workspace_id=%s for key share"

# Set-based bulk INSERT (issue #448 phase 1): one unnest-driven INSERT per
# batch instead of psycopg's executemany (still N independent statements
# server-side). The v77 statement triggers then aggregate a whole batch into
# ONE counter upsert per (key, status) — the per-row trigger firings of the
# executemany shape were the remaining bulk-intake cost after #437. psycopg
# adapts each column list as ONE array parameter (14 params per statement,
# regardless of row count), so the 65535 extended-protocol parameter limit
# is never in play; batch size bounds the server-side memory the unnest
# arrays plus a batch's transition tables occupy; one transaction still
# wraps every batch, same as the executemany shape.
_JOBS_BATCH_ROWS = 1000

# Duplicate job ids inside one call (same source_id listed twice, or two
# entity_ids normalizing onto one id): ``on conflict do update`` is set-based
# — two same-id rows in one unnest batch are a CardinalityViolation, where
# executemany's N separate statements let the later row's DO UPDATE win over
# the earlier one. The dict below keeps the LAST row per id, restoring that
# later-row-wins semantics before the rows reach SQL (#461 review).
# Residual difference vs the executemany shape (accepted): the old shape
# fired the v77 statement trigger TWICE for a duplicated id (earlier row's
# INSERT followed by the later row's UPDATE, bumping updated_at); the dedup
# emits it once as a single INSERT.
_JOBS_BULK_INSERT_SQL = """
insert into jobs(
  id, workspace_id, source_type, source_id, run_id, title, storage_dir, stem,
  workflow_revision_id, workflow_version, workflow_definition_hash,
  workflow_definition_snapshot_json, input_json, frozen_config_json
)
select * from unnest(
  %s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],
  %s::text[], %s::text[], %s::int[], %s::text[], %s::text[], %s::text[], %s::text[]
)
on conflict(id) do update set
  title=excluded.title, stem=excluded.stem, run_id=excluded.run_id,
  input_json=excluded.input_json, frozen_config_json=excluded.frozen_config_json, updated_at=current_timestamp
"""

_JOB_NODES_BULK_INSERT_SQL = """
insert into job_nodes(job_id, node_key, status, created_at)
select c, k, 'pending', current_timestamp from unnest(%s::text[], %s::text[]) as t(c, k)
on conflict(job_id, node_key) do nothing
"""


def _insert_jobs_batched(conn: Any, rows: list[tuple[Any, ...]]) -> None:
    # list per column (not tuple): psycopg adapts a tuple as one record value
    # and a list as a 1-D array — unnest needs the arrays (same arity rows,
    # so the strict zip cannot fail).
    for start in range(0, len(rows), _JOBS_BATCH_ROWS):
        chunk = rows[start : start + _JOBS_BATCH_ROWS]
        conn.execute(_JOBS_BULK_INSERT_SQL, [list(column) for column in zip(*chunk, strict=True)])


def _insert_job_nodes_batched(conn: Any, pairs: list[tuple[str, str]]) -> None:
    for start in range(0, len(pairs), _JOBS_BATCH_ROWS):
        chunk = pairs[start : start + _JOBS_BATCH_ROWS]
        columns = [list(column) for column in zip(*chunk, strict=True)]
        conn.execute(_JOB_NODES_BULK_INSERT_SQL, columns)


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
        rows: dict[str, tuple[Any, ...]] = {}
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
            if job_id not in rows:
                job_ids.append(job_id)
            input_doc = candidate_input(candidate)
            if input_doc.get("type") == "material":
                material_ids.append(str(input_doc.get("material_id") or ""))
            elif input_doc.get("type") == "bundle":
                bundle_ids.append(str(input_doc.get("bundle_id") or ""))
            # dict-keyed insert = dedup by job id, last row wins (the
            # executemany shape's later-DO-UPDATE-wins semantics — see
            # _JOBS_BULK_INSERT_SQL's comment).
            rows[job_id] = (
                job_id,
                workspace_id,
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
        row_list = list(rows.values())

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
            for row in row_list:
                current = by_id.get(str(row[0]))
                if current is not None and (
                    current["workspace_id"] != row[1]
                    or current["source_type"] != row[2]
                    or current["source_id"] != row[3]
                ):
                    raise ValueError(f"Job identity collision for {row[0]}")
            for row in row_list:
                job_id = str(row[0])
                if job_id not in by_id:
                    storage_dirs[job_id].mkdir(parents=True, exist_ok=True)
            # Set-based INSERT: ON CONFLICT arm and column values are
            # byte-identical to the old executemany shape — only the
            # statement granularity changes (see _JOBS_BATCH_ROWS). Batch-
            # internal duplicate ids were deduplicated above (executemany's
            # later-row-wins semantics preserved).
            _insert_jobs_batched(conn, row_list)
            _insert_job_nodes_batched(
                conn, [(job_id, node_key) for job_id in job_ids for node_key in node_keys]
            )
            created = conn.execute(
                f"select * from jobs where id in ({placeholders})", job_ids
            ).fetchall()
        created_by_id = {str(row["id"]): dict[str, Any](dict(row)) for row in created}
        # #211 M2: the jobs column is gone (v70); callers that surface rows on
        # the wire keep the deprecated workflow_key field via the identity
        # value (workflow_key == workspace_id since v62).
        for created_row in created_by_id.values():
            created_row.setdefault("workflow_key", workspace_id)
        return [created_by_id[job_id] for job_id in job_ids]
