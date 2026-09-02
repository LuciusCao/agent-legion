"""Data migration applied alongside the idempotent DDL replay (v72)."""

from __future__ import annotations

from typing import Any

# Run job status counts (schema v72, #358): count_jobs_by_status_in_run feeds
# the run detail endpoint's job_stats (and, later, the #350 run progress
# view). As a group-by over the run's whole jobs slice it is O(run jobs) per
# call; a 10^6-item run turned every run-detail refresh into a million-row
# scan. The counter table is maintained transactionally by a row trigger on
# jobs (DB-RUN-JOB-STATUS-COUNTS-001), turning the read into a PK lookup of
# a handful of rows regardless of run size.
#
# The trigger DDL lives HERE, not in postgres_schema.sql: the schema file
# replays before the v53 migrate_runs renames jobs.batch_id -> run_id, and a
# trigger function reading NEW.run_id would fail on every jobs write of a
# v52-shape database — the same replay-order rule as idx_jobs_run_id (v59).
# At v72 the rename has happened on every upgrade path (v53 < v72 in the
# version-sorted chain of the same init_db transaction).
_TRIGGER_DDL = """
create or replace function sync_run_job_status_counts() returns trigger as $$
begin
  if TG_OP = 'INSERT' then
    if NEW.run_id <> '' then
      insert into run_job_status_counts(run_id, status, cnt)
      values (NEW.run_id, NEW.status, 1)
      on conflict (run_id, status)
      do update set cnt = run_job_status_counts.cnt + 1;
    end if;
    return NEW;
  elsif TG_OP = 'DELETE' then
    if OLD.run_id <> '' then
      update run_job_status_counts set cnt = cnt - 1
      where run_id = OLD.run_id and status = OLD.status;
    end if;
    return OLD;
  else
    if NEW.run_id is distinct from OLD.run_id
       or NEW.status is distinct from OLD.status then
      if OLD.run_id <> '' then
        update run_job_status_counts set cnt = cnt - 1
        where run_id = OLD.run_id and status = OLD.status;
      end if;
      if NEW.run_id <> '' then
        insert into run_job_status_counts(run_id, status, cnt)
        values (NEW.run_id, NEW.status, 1)
        on conflict (run_id, status)
        do update set cnt = run_job_status_counts.cnt + 1;
      end if;
    end if;
    return NEW;
  end if;
end;
$$ language plpgsql;
drop trigger if exists jobs_run_status_counts_sync on jobs;
create trigger jobs_run_status_counts_sync
  after insert or delete or update of status, run_id on jobs
  for each row execute function sync_run_job_status_counts();
"""

# The backfill rebuilds the table from jobs; on-conflict replace makes the
# whole migration idempotent and self-healing on replay. Jobs with run_id=''
# have no run and are excluded (the read path never queries them by run).
_BACKFILL_SQL = """
insert into run_job_status_counts(run_id, status, cnt)
select run_id, status, count(*) from jobs where run_id <> '' group by 1, 2
on conflict (run_id, status) do update set cnt = excluded.cnt
"""


def migrate_run_job_status_counts(conn: Any) -> None:
    """Create the run counter trigger and backfill it from jobs (v72)."""
    conn.execute(_TRIGGER_DDL)
    conn.execute(_BACKFILL_SQL)
