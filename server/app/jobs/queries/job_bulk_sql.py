"""SQL constants and lock/insert statement helpers for job_bulk (#448/#467).

Split out of ``job_bulk.py`` for the file-size budget (same precedent as
``batch_queue_sql.py``).
"""

from __future__ import annotations

from typing import Any

# Chunk = one committed transaction (lock probe + insert + node rows); a
# mid-run failure leaves whole committed chunks behind (see create_jobs_bulk).
# 1000 = the pre-chunking statement batch: each chunk's unnest INSERT still
# fires the v77 trigger once (#448).
CHUNK_ROWS = 1000

MATERIAL_LOCK_IN_SQL = (
    "select id from materials where workspace_id=%s and id in ({ids}) order by id for key share"
)
BUNDLE_LOCK_IN_SQL = (
    "select id from material_bundles where workspace_id=%s and id in ({ids})"
    " order by id for key share"
)

# Set-based bulk INSERT (#448): one unnest statement per batch (executemany's
# N statements fired the v77 trigger per row); each column list is ONE array
# parameter, so the extended-protocol parameter limit is never in play.
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
    """FOR KEY SHARE-lock a chunk's referenced rows, inside that chunk's own
    transaction and before its INSERT (per-chunk lock-before-insert, review
    P1-1; the lock is released by that chunk's commit — the next chunk
    re-probes its own refs).

    Delete orderings against a chunk, all safe: (a) committed before the
    probe → row missing → ValueError → the run service's partial-failure
    path — no job references a deleted row; (b) holding FOR UPDATE → the
    probe blocks, then sees (a); (c) probe locks first → the delete blocks
    until the chunk commits, then its reference check rejects it (409).
    ORDER BY id keeps multi-row acquisition deterministic (protects bulk
    FOR UPDATE deleters from lock inversion); cross-run FOR KEY SHARE probes
    are mutually compatible — no cross-run deadlock surface.
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
