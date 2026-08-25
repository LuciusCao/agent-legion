"""Schema v54: job_artifacts table (job artifacts in object storage, D12)."""

from __future__ import annotations

from typing import Any

# Idempotent on replay: the table create is guarded by IF NOT EXISTS. Rows
# are written only by the artifact upload service; there is no backfill —
# legacy artifacts stay in the local job_dir and resolve via local fallback.
_JOB_ARTIFACTS_DDL = """
create table if not exists job_artifacts (
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  name text not null,
  storage_key text not null,
  size_bytes bigint not null,
  content_hash text not null default '',
  uploaded_at timestamptz not null default current_timestamp,
  primary key(job_id, node_key, name)
)
"""


def migrate_job_artifacts(conn: Any) -> None:
    """Create the job_artifacts manifest table (v54)."""
    conn.execute(_JOB_ARTIFACTS_DDL)
