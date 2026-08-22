RUN_UPSERT_CONFLICT = """
on conflict(id) do update set
  status=case
    when excluded.status='queued' and runs.status='failed' then 'queued'
    else runs.status
  end,
  error_message=case
    when excluded.status='queued' and runs.status='failed' then ''
    else runs.error_message
  end,
  updated_at=current_timestamp
"""

# Atomic requeue of a completed run whose jobs were (partially) deleted: the
# guarded update matches only while the run is still completed and its live
# job count is below the count recorded at completion, so a concurrent intake
# consumer claim or a duplicate re-submit turns it into a no-op.
RUN_REQUEUE_DEPLETED = """
update runs
set queue_payload_json=%s, status='queued', error_message='', updated_at=current_timestamp,
    created_count=(select count(*) from jobs where run_id=%s)
where id=%s and status='completed'
  and (select count(*) from jobs where run_id=%s) < %s
returning *
"""
