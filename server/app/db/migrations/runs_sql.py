"""Set-based SQL for the runs cutover (schema v53), split from ``runs.py``.

The data backfill is set-based SQL (one INSERT + two UPDATEs) instead of
per-row Python loops: production ``jobs`` holds ~260k rows and the whole
migration runs inside the single ``init_db`` startup transaction, so row-by-row
UPDATEs would hold row locks until commit and stretch the startup outage.
Payload decoding happens in ``pg_temp._runs_payload`` (invalid or non-object
JSON degrades to ``{}``, mirroring the old Python ``_decode``).
"""

from __future__ import annotations

# Session-scoped payload decoder: invalid JSON or a non-object document
# degrades to ``{}`` (same semantics as the old per-row Python decode).
_DECODE_FN_DDL = """
create or replace function pg_temp._runs_payload(raw text) returns jsonb
language plpgsql as $$
declare
  doc jsonb;
begin
  doc := raw::jsonb;
  if jsonb_typeof(doc) = 'object' then
    return doc;
  end if;
  return '{}'::jsonb;
exception when others then
  return '{}'::jsonb;
end
$$
"""

# Every batch becomes one run row; the payload's pin keys make up
# frozen_pins_json, and queued async intake runs keep their whole payload.
_INSERT_RUNS_SQL = """
insert into runs(
  id, workspace_id, workflow_key, source_kind, status, frozen_pins_json,
  queue_payload_json, created_count, error_message, created_at, updated_at
)
select
  b.id,
  b.workspace_id,
  b.workflow_key,
  b.source_kind,
  b.status,
  coalesce(
    (select jsonb_object_agg(pin.key, pin.value)
     from jsonb_each(p.doc) as pin(key, value)
     where pin.key in ('node_code_versions', 'agent_versions', 'quality_replay')),
    '{}'::jsonb
  )::text,
  case
    when jsonb_typeof(p.doc -> '_intake_queue') = 'object' then b.source_payload_json
    else ''
  end,
  b.created_count,
  b.error_message,
  b.created_at,
  b.updated_at
from job_batches b
cross join lateral (select pg_temp._runs_payload(b.source_payload_json) as doc) p
on conflict(id) do nothing
"""

# The frozen node_config sinks onto every job of the batch (non-empty objects
# only; jobs already carrying a config keep it).
_SINK_FROZEN_CONFIG_SQL = """
update jobs j
set frozen_config_json = (p.doc -> 'node_config')::text
from job_batches b
cross join lateral (select pg_temp._runs_payload(b.source_payload_json) as doc) p
where j.run_id = b.id
  and j.frozen_config_json is null
  and jsonb_typeof(p.doc -> 'node_config') = 'object'
  and p.doc -> 'node_config' <> '{}'::jsonb
"""

# Every job gets the minimal legacy ref input, enriched with the matched
# candidate's entity_type/title/stem when present. Candidates are matched by
# entity_id against jobs.source_id; duplicate entity_ids keep the last
# occurrence (same as the old dict overwrite). Jobs already carrying an input
# keep it.
_SINK_INPUTS_SQL = """
with candidates as (
  select
    b.id as batch_id,
    cand.value ->> 'entity_id' as source_id,
    (select jsonb_object_agg(extra.key, extra.value)
     from (values
       ('entity_type', cand.value ->> 'entity_type'),
       ('title', cand.value ->> 'title'),
       ('stem', cand.value ->> 'stem')
     ) as extra(key, value)
     where extra.value is not null and extra.value <> '') as extras,
    cand.position
  from job_batches b
  cross join lateral (
    select pg_temp._runs_payload(b.source_payload_json) as doc
  ) p
  cross join lateral jsonb_array_elements(
    case
      when jsonb_typeof(p.doc -> 'task_candidates') = 'array'
        then p.doc -> 'task_candidates'
      else '[]'::jsonb
    end
  ) with ordinality as cand(value, position)
  where jsonb_typeof(cand.value) = 'object'
    and cand.value ->> 'entity_id' is not null
),
matched as (
  select distinct on (batch_id, source_id)
    batch_id, source_id, extras
  from candidates
  order by batch_id, source_id, position desc
)
update jobs j
set input_json = (
  jsonb_build_object(
    'type', 'ref',
    'connection_key', '',
    'external_id', j.source_id,
    'legacy', true
  ) || coalesce(
    (select m.extras
     from matched m
     where m.batch_id = j.run_id and m.source_id = j.source_id),
    '{}'::jsonb
  )
)::text
where j.input_json is null
  and exists (select 1 from job_batches b where b.id = j.run_id)
"""
