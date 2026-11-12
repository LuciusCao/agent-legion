"""Schema v104 (#1363): batch approval audit context and operation kinds."""

from typing import Any

_DDL = """
alter table approval_decisions
  add column if not exists payload_json text not null default '{}',
  add column if not exists batch_id text,
  add column if not exists batch_selection text;
alter table approval_decisions drop constraint if exists approval_decisions_batch_context_check;
alter table approval_decisions add constraint approval_decisions_batch_context_check
  check (
    (batch_id is null and batch_selection is null)
    or (batch_id is not null and batch_selection in ('ids', 'filter'))
  );
create index if not exists idx_approval_decisions_batch
  on approval_decisions(batch_id) where batch_id is not null;
alter table job_batch_operations drop constraint if exists job_batch_operations_kind_check;
alter table job_batch_operations add constraint job_batch_operations_kind_check
  check(kind in (
    'rerun', 'rerun_by_failure', 'delete', 'run_to',
    'pause', 'resume', 'upgrade_workflow', 'package', 'clear_packed',
    'approve', 'reject'));
"""


def migrate_approval_batch(conn: Any) -> None:
    """Add batch approval audit fields and kinds; idempotent on replay."""
    conn.execute(_DDL)
