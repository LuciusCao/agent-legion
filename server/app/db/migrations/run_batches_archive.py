"""Schema v100 (#1157 phase3): ``run_batches.archived_at`` — 任务中心归档位。

The Studio task center (M44) lists run_batches in three views — active,
ended, archived (docs/architecture/runs-async-mode.md §6). Manual archive
stamps this column; auto-archive is a query-time reading (terminal rows
whose finished_at is older than 7 days fall out of the ended view into the
archived view, no sweeper writes). The retention sweep deletes archived
rows past 90 days (manual: archived_at; auto: finished_at + 7d + 90d).

Same guarded-ALTER home rule as v87+: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay,
and both install paths run the chain anyway. The partial index serves the
archived view's keyset scan and the retention sweep's cutoff probe.
"""

from __future__ import annotations

from typing import Any

# Re-exported so migration_chain_recent imports both run_batches migrations
# from one module (that file sits inside its growth-allowance band; a second
# import line would tip it over).
from server.app.db.migrations.run_batches import migrate_run_batches

__all__ = ["migrate_run_batches", "migrate_run_batches_archive"]

_RUN_BATCHES_ARCHIVE_DDL = """
alter table run_batches
  add column if not exists archived_at timestamptz;
create index if not exists idx_run_batches_archived
  on run_batches(archived_at) where archived_at is not null;
"""


def migrate_run_batches_archive(conn: Any) -> None:
    """Add the archive column + partial index (v100, #1157); idempotent."""
    conn.execute(_RUN_BATCHES_ARCHIVE_DDL)
