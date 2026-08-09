BATCH_UPSERT_CONFLICT = """
on conflict(id) do update set
  status=case
    when excluded.status='queued' and job_batches.status='failed' then 'queued'
    else job_batches.status
  end,
  error_message=case
    when excluded.status='queued' and job_batches.status='failed' then ''
    else job_batches.error_message
  end,
  updated_at=current_timestamp
"""

# Atomic requeue of a completed batch whose jobs were (partially) deleted: the
# guarded update matches only while the batch is still completed and its live
# job count is below the count recorded at completion, so a concurrent intake
# consumer claim or a duplicate re-submit turns it into a no-op.
BATCH_REQUEUE_DEPLETED = """
update job_batches
set source_payload_json=%s, status='queued', error_message='', updated_at=current_timestamp,
    created_count=(select count(*) from jobs where batch_id=%s)
where id=%s and status='completed'
  and (select count(*) from jobs where batch_id=%s) < %s
returning *
"""
