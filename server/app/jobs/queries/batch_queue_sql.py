# Frozen pins refresh only while the conflicting run still owns no jobs: a
# run whose jobs were deleted (e.g. code republished, then the same items
# resubmitted) must not keep the stale pins a quality replay would freeze to,
# while a run with live jobs keeps the pins those jobs were created with. The
# async intake queue carrier also has no jobs yet at upsert time, so its pins
# refresh on resubmission — the expected behavior there.
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
  frozen_pins_json=case when not exists (select 1 from jobs where run_id=runs.id)
    then excluded.frozen_pins_json else runs.frozen_pins_json end,
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
