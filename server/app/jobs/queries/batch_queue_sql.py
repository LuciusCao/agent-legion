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
