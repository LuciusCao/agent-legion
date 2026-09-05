"""SQL constants and lock/insert statement helpers for job_bulk (#448/#467).

Split out of ``job_bulk.py`` for the file-size budget (same precedent as
``batch_queue_sql.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.jobs.run_freeze import candidate_input
from server.app.jobs.storage_layout import job_storage_dir
from server.app.storage_paths import make_data_relative

# Chunk = one committed transaction (insert + node rows + FOR KEY SHARE
# locks); a mid-run failure leaves whole committed chunks behind (recovery:
# see create_jobs_bulk). 1000 rows = the pre-chunking statement batch, so
# each chunk's unnest INSERT still fires the v77 trigger once (#448).
CHUNK_ROWS = 1000

MATERIAL_LOCK_IN_SQL = (
    "select id from materials where workspace_id=%s and id in ({ids}) order by id for key share"
)
BUNDLE_LOCK_IN_SQL = (
    "select id from material_bundles where workspace_id=%s and id in ({ids})"
    " order by id for key share"
)

# Set-based bulk INSERT (#448 phase 1): one unnest-driven INSERT per batch
# (executemany's N statements fired the v77 trigger per row); psycopg adapts
# each column list as ONE array parameter, so the extended-protocol
# parameter limit is never in play.
JOBS_BULK_INSERT_SQL = """
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

JOB_NODES_BULK_INSERT_SQL = """
insert into job_nodes(job_id, node_key, status, created_at)
select c, k, 'pending', current_timestamp from unnest(%s::text[], %s::text[]) as t(c, k)
on conflict(job_id, node_key) do nothing
"""


def insert_jobs_batched(conn: Any, rows: list[tuple[Any, ...]]) -> None:
    # list per column (not tuple): psycopg adapts a tuple as one record value
    # and a list as a 1-D array — unnest needs the arrays (same arity rows,
    # so the strict zip cannot fail).
    for start in range(0, len(rows), CHUNK_ROWS):
        chunk = rows[start : start + CHUNK_ROWS]
        conn.execute(JOBS_BULK_INSERT_SQL, [list(column) for column in zip(*chunk, strict=True)])


def insert_job_nodes_batched(conn: Any, pairs: list[tuple[str, str]]) -> None:
    for start in range(0, len(pairs), CHUNK_ROWS):
        chunk = pairs[start : start + CHUNK_ROWS]
        columns = [list(column) for column in zip(*chunk, strict=True)]
        conn.execute(JOB_NODES_BULK_INSERT_SQL, columns)


def lock_rows_for_key_share(
    conn: Any, sql_template: str, ids: list[str], workspace_id: str, *, kind: str
) -> None:
    """FOR KEY SHARE-lock the referenced rows, in chunked IN probes.

    All referenced ids of the whole call are locked in ONE statement per
    source table (id list chunked to bound placeholder count), *before* any
    INSERT runs — the pre-chunking protocol serialized delete against the
    whole insert, and per-chunk locking would open a window where a delete
    interleaves between two chunks: the earlier chunk's jobs would already
    reference the deleted row. The ORDER BY id makes lock acquisition order
    deterministic across every caller, so two concurrent runs cannot
    deadlock on the same material set. Rows are locked one table at a time
    in the fixed order materials → bundles → jobs.
    """
    for start in range(0, len(ids), CHUNK_ROWS):
        chunk = ids[start : start + CHUNK_ROWS]
        placeholders = ",".join("%s" for _ in chunk)
        rows = conn.execute(
            sql_template.format(ids=placeholders), [workspace_id, *chunk]
        ).fetchall()
        if len(rows) != len(set(chunk)):
            found = {str(row["id"]) for row in rows}
            missing = sorted(set(chunk) - found)
            raise ValueError(f"{kind} not found: {', '.join(missing[:3])}")


def job_row_tuple(
    jobs_dir: Path,
    workspace_id: str,
    workflow_key: str,
    run_id: str,
    revision: dict[str, Any],
    candidate: dict[str, Any],
    source_id: str,
    job_id: str,
    frozen_config_json: str | None,
) -> tuple[Any, ...]:
    """The jobs-row insert tuple for one candidate (column order = the
    unnest INSERT's column list)."""
    return (
        job_id,
        workspace_id,
        str(candidate["entity_type"]),
        source_id,
        run_id,
        str(candidate["title"]),
        make_data_relative(job_storage_dir(jobs_dir, workspace_id, job_id), jobs_dir.parent),
        str(candidate.get("stem", "")),
        revision["id"],
        int(revision["version"]),
        revision["definition_hash"],
        revision["definition_json"],
        json.dumps(candidate_input(candidate), ensure_ascii=False),
        frozen_config_json,
    )
