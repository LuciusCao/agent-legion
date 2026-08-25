create table if not exists workspaces (
  id text primary key,
  name text not null,
  default_workflow_key text not null,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  cms_config_json text not null default '{}',
  resource_config_json text not null default '{}',
  node_config_json text not null default '{}',
  default_entity text not null default 'question',
  intake_config_json text not null default '{}',
  description text not null default '',
  default_agent_provider text not null default '',
  default_agent_model text not null default '',
  default_agent_thinking text not null default ''
);

-- Idempotent upgrade path for databases created before schema v14:
-- `create table if not exists` above does not add columns to existing tables.
alter table workspaces add column if not exists node_config_json text not null default '{}';

-- The platform ships no default workflow: existing databases keep their
-- stored values, only the column default is dropped.
alter table workspaces alter column default_workflow_key drop default;

-- Executor allocations/bindings (retired at schema v47): the tables are
-- still created here so the historical v17/v18 data migrations can replay
-- on fresh databases; migrate_executor_retirement harvests their contents
-- and drops both at the end of the migration chain (same pattern as the
-- workspaces.cms_config_json column, created here and dropped post-chain).
create table if not exists workspace_executor_allocations (
  workspace_id text not null references workspaces(id) on delete cascade,
  executor_id text not null,
  concurrency_limit integer not null check(concurrency_limit > 0),
  primary key(workspace_id, executor_id)
);

create table if not exists workspace_node_bindings (
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  executor_id text not null,
  primary key(workspace_id, workflow_key, node_key)
);

create table if not exists workspace_node_limits (
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  concurrency_limit integer not null check(concurrency_limit > 0),
  primary key(workspace_id, workflow_key, node_key)
);

create table if not exists workspace_node_routes (
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  target_kind text not null check(target_kind in ('handler_executor', 'agent')),
  target_id text not null,
  primary key(workspace_id, workflow_key, node_key)
);

create table if not exists workspace_node_capacities (
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  max_concurrency integer not null check(max_concurrency > 0),
  source_revision_id text not null default '',
  updated_at timestamptz not null default current_timestamp,
  primary key(workspace_id, workflow_key, node_key)
);

-- Workspace-level Agent execution capacity (supersedes per-node
-- workspace_node_capacities for Agent routing; the node table remains only
-- as a legacy projection that publish now prunes). A workspace WITHOUT a row
-- has no configured Agent limit and is treated as unlimited at claim time.
create table if not exists workspace_agent_capacities (
  workspace_id text primary key references workspaces(id) on delete cascade,
  max_concurrency integer not null check(max_concurrency > 0),
  updated_at timestamptz not null default current_timestamp
);

-- job_batches (retired at schema v53, materials-and-runs design §5.2/§8):
-- still created here so the historical data migrations that replay against it
-- (e.g. migrate_external_connections' payload rewrite) keep working on fresh
-- databases; migrate_runs harvests every row into runs + jobs freeze columns
-- and drops the table at the end of the migration chain (same pattern as the
-- executor allocation/binding tables retired at v47).
create table if not exists job_batches (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  source_kind text not null,
  source_payload_json text not null default '{}',
  status text not null default 'created',
  created_count integer not null default 0,
  error_message text not null default '',
  created_at timestamptz not null default current_timestamp
);

-- Runs (schema v53, route A rename of job_batches): one run = a batch of
-- items x one workflow execution. frozen_pins_json carries the quality-replay
-- pins (node_code_versions / agent_versions / quality_replay marker);
-- queue_payload_json is the async intake working state (input values, chunk
-- cursor), only set for queued/processing intake runs and retired with the
-- intake queue (INTAKE-RETIRE-001). Per-job frozen inputs/configs live on the
-- jobs row itself (RUN-FREEZE-001).
create table if not exists runs (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  source_kind text not null default '',
  status text not null default 'created',
  frozen_pins_json text not null default '{}',
  stats_json text not null default '{}',
  queue_payload_json text not null default '',
  created_count integer not null default 0,
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
);

create table if not exists jobs (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  source_type text not null,
  source_id text not null,
  run_id text not null default '',
  input_json text,
  frozen_config_json text,
  title text not null default '',
  status text not null default 'queued',
  storage_dir text not null default '',
  error_message text not null default '',
  stem text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  execution_mode text not null default 'full' check(execution_mode in ('full', 'until_node')),
  target_node_key text,
  execution_paused integer not null default 0 check(execution_paused in (0, 1)),
  pause_reason text not null default '',
  packed integer not null default 0,
  workflow_revision_id text not null default '',
  workflow_definition_hash text not null default '',
  workflow_definition_snapshot_json text not null default '',
  outcome text not null default '',
  workflow_version integer
);

create table if not exists job_nodes (
  id bigint generated by default as identity primary key,
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  status text not null default 'pending',
  stale_reason text not null default '',
  error_message text not null default '',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default current_timestamp,
  unique(job_id, node_key)
);

create table if not exists node_runs (
  id bigint generated by default as identity primary key,
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  status text not null,
  started_at timestamptz not null default current_timestamp,
  finished_at timestamptz,
  command_json text not null default '[]',
  exit_code integer,
  log_path text not null default '',
  error_message text not null default '',
  run_dir text not null default '',
  session_dir text not null default '',
  skill_version text not null default '',
  runner text not null default ''
);

create table if not exists executor_leases (
  id text primary key,
  execution_id text not null unique,
  executor_id text not null,
  workspace_id text not null references workspaces(id) on delete cascade,
  job_id text not null references jobs(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  node_run_id bigint not null references node_runs(id) on delete cascade,
  status text not null check(status in ('active', 'released', 'expired')),
  acquired_at timestamptz not null,
  heartbeat_at timestamptz not null,
  expires_at timestamptz not null
);

create table if not exists workspace_packages (
  id bigint generated by default as identity primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  path text not null,
  name text not null default '',
  job_count integer not null default 0,
  size_bytes bigint not null default 0,
  locked integer not null default 0,
  created_at timestamptz not null default current_timestamp
);

create table if not exists workflow_revisions (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  version integer not null,
  status text not null check(status in ('draft', 'active', 'archived')),
  definition_json text not null,
  definition_hash text not null,
  created_at timestamptz not null default current_timestamp,
  published_at timestamptz,
  unique(workspace_id, workflow_key, version)
);

-- Workflow catalog (schema v40) is retired at schema v50 (issue #112): the
-- global workflow key registry was the last global concept on the execution
-- path; a workflow is now just the DAG inside one workspace
-- (workspaces.default_workflow_key is a plain text identifier). The table is
-- no longer created here; migrate_workflow_catalog_retirement drops it on
-- existing databases.

create table if not exists node_run_token_usage (
  id bigint generated by default as identity primary key,
  node_run_id bigint not null unique references node_runs(id) on delete cascade,
  job_id text not null references jobs(id) on delete cascade,
  workspace_id text not null references workspaces(id) on delete cascade,
  node_key text not null,
  provider text not null default '',
  model text not null default '',
  skill_version text not null default '',
  message_count integer not null default 0,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cache_read_tokens bigint not null default 0,
  total_tokens bigint not null default 0,
  usage_source text not null default 'events_jsonl',
  is_complete integer not null default 1 check(is_complete in (0, 1)),
  parse_error text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
);

create table if not exists agent_workers (
  worker_id text primary key,
  name text not null default '',
  runtimes_json text not null,
  capabilities_json text not null default '[]',
  models_json text not null default '[]',
  max_concurrency integer not null check(max_concurrency > 0),
  -- Code-execution capacity pool (schema v39): 0 = agent-only Worker.
  max_code_concurrency integer not null default 0 check(max_code_concurrency >= 0),
  labels_json text not null default '{}',
  protocol_version integer not null,
  token_hash text not null,
  -- Server-resolved workspace admission scope: '[]' means all workspaces
  -- (the pre-v7 behavior for already-registered workers). Populated only from
  -- the registration credential (global register token or scoped token),
  -- never from Worker-supplied fields.
  allowed_workspaces_json text not null default '[]',
  registered_at timestamptz not null,
  last_seen_at timestamptz not null,
  revoked_at timestamptz
);
alter table agent_workers add column if not exists allowed_workspaces_json text not null default '[]';
alter table agent_workers add column if not exists capabilities_json text not null default '[]';
alter table agent_workers add column if not exists models_json text not null default '[]';
alter table agent_workers add column if not exists max_code_concurrency integer not null default 0;
alter table agent_workers drop constraint if exists agent_workers_max_code_concurrency_check;
alter table agent_workers add constraint agent_workers_max_code_concurrency_check
  check(max_code_concurrency >= 0);

-- Workspace-scoped Agent Worker registration tokens (EXEC-WORKERACL-001).
-- workspace_id NULL means the token admits Workers to ALL workspaces; a
-- scoped row admits only its workspace. Only token_hash is stored; the
-- plaintext is returned exactly once at issuance.
create table if not exists agent_register_tokens (
  id text primary key,
  token_hash text not null,
  workspace_id text references workspaces(id) on delete cascade,
  label text not null default '',
  created_at timestamptz not null default current_timestamp,
  revoked_at timestamptz
);

create table if not exists agent_execution_requests (
  execution_id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  job_id text not null references jobs(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  -- Request flavor (schema v39): 'agent' rows carry an Agent payload and join
  -- versioned_entities at claim; 'code' rows carry a self-contained code
  -- payload (code text + hash in the bundle) and skip that join.
  kind text not null default 'agent'
    check(kind in ('agent', 'code')),
  agent_id text not null,
  agent_definition_hash text not null,
  node_concurrency_limit integer not null check(node_concurrency_limit > 0),
  state text not null default 'queued'
    check(state in ('queued', 'claimed', 'reporting', 'done', 'cancelled')),
  worker_id text,
  lease_id text,
  node_run_id bigint,
  attempt integer not null default 0,
  queued_at timestamptz not null,
  claimed_at timestamptz,
  heartbeat_at timestamptz,
  finished_at timestamptz,
  manifest_json text not null,
  outcome_json text
);
alter table agent_execution_requests add column if not exists lease_id text;
alter table agent_execution_requests add column if not exists node_run_id bigint;
alter table agent_execution_requests add column if not exists kind text not null default 'agent';
alter table agent_execution_requests drop constraint if exists agent_execution_requests_kind_check;
alter table agent_execution_requests add constraint agent_execution_requests_kind_check
  check(kind in ('agent', 'code'));
-- State evolution: 'reporting' (result upload pending; execution slot released).
-- Drop/re-add so databases created before this state existed pick it up.
alter table agent_execution_requests drop constraint if exists agent_execution_requests_state_check;
alter table agent_execution_requests add constraint agent_execution_requests_state_check
  check(state in ('queued', 'claimed', 'reporting', 'done', 'cancelled'));

create index if not exists idx_agent_requests_claim
  on agent_execution_requests(state, queued_at, execution_id);
-- Claim candidate lookup walks only the per-workspace queued head (schema
-- v18); a full queued scan priced every row and made claims O(queue depth).
-- kind joined the key at schema v51 (issue #125): the claim scan is per
-- kind, so each kind walks its own (workspace_id, kind) window.
create index if not exists idx_agent_requests_queued_head
  on agent_execution_requests(workspace_id, kind, queued_at, execution_id)
  where state = 'queued';
create index if not exists idx_agent_requests_node_active
  on agent_execution_requests(workspace_id, workflow_key, node_key, state);
create index if not exists idx_agent_requests_worker_active
  on agent_execution_requests(worker_id, state);
-- One active request per node; 'reporting' still owns the node until the
-- result commits, so it must block re-enqueue too.
drop index if exists idx_agent_requests_one_active_node;
create unique index if not exists idx_agent_requests_one_active_node
  on agent_execution_requests(job_id, node_key)
  where state in ('queued', 'claimed', 'reporting');
-- Stockpile gate's done-rate window scan (schema v20,
-- server.app.workflow_worker.agent_stock); partial, so only done rows pay for it.
create index if not exists idx_agent_requests_done_recent
  on agent_execution_requests(finished_at)
  where state = 'done';
-- Bundle reaper's incremental window scan (schema v33,
-- server.app.agent_broker.reaper): the cancelled branch mirrors the done
-- partial index so neither branch degenerates into a full-table seq scan.
create index if not exists idx_agent_requests_cancelled_recent
  on agent_execution_requests(finished_at)
  where state = 'cancelled';
-- Ops-metrics recent-hour run stats (schema v37,
-- server.app.services._ops_metrics_runs): the semi join attributing a run to
-- an Agent execution matches on r.node_run_id; without this index Postgres
-- hashes the whole requests table (660k rows, ~0.9s measured) on every
-- /api/metrics/overview poll.
create index if not exists idx_agent_requests_node_run
  on agent_execution_requests(node_run_id);

create table if not exists job_event_seq (
  id integer primary key check(id = 1),
  value bigint not null
);
insert into job_event_seq(id, value) values (1, 0) on conflict(id) do nothing;

create table if not exists worker_control_state (
  scope text primary key,
  paused integer not null default 1,
  updated_by text not null,
  updated_at timestamptz not null
);

-- Global (non-workspace) product settings, e.g. token_usage pricing (schema v21).
-- value holds a JSON document whose shape mirrors the yaml fallback section.
create table if not exists global_settings (
  key text primary key,
  value text not null,
  updated_at timestamptz not null default current_timestamp
);

create table if not exists artifacts (
  hash text primary key,
  size bigint not null,
  created_at timestamptz not null default current_timestamp
);

create table if not exists artifact_refs (
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  name text not null,
  hash text not null references artifacts(hash),
  primary key(job_id, node_key, name)
);

-- Job artifacts uploaded to the instance object store (schema v54, D12):
-- the authoritative manifest of produced artifacts; the local job_dir copy
-- is an evictable cache. storage_key never leaves the server.
create table if not exists job_artifacts (
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  name text not null,
  storage_key text not null,
  size_bytes bigint not null,
  content_hash text not null default '',
  uploaded_at timestamptz not null default current_timestamp,
  primary key(job_id, node_key, name)
);

create table if not exists node_shards (
  job_id text not null references jobs(id) on delete cascade,
  node_key text not null,
  shard_index integer not null,
  status text not null default 'pending',
  input_json text not null default '{}',
  output_json text not null default '',
  error_message text not null default '',
  execution_id text not null default '',
  started_at timestamptz,
  finished_at timestamptz,
  primary key(job_id, node_key, shard_index)
);

create index if not exists idx_workspaces_created_at on workspaces(created_at);
create index if not exists idx_job_batches_workspace on job_batches(workspace_id, created_at);
alter table job_batches
  add column if not exists updated_at timestamptz not null default current_timestamp;
create index if not exists idx_job_batches_intake_queue
  on job_batches(status, updated_at) where status in ('queued', 'processing');
create index if not exists idx_runs_workspace on runs(workspace_id, created_at);
create index if not exists idx_runs_intake_queue
  on runs(status, updated_at) where status in ('queued', 'processing');
create index if not exists idx_jobs_workflow_status on jobs(workflow_key, status);
-- Workflow worker incremental scan (list_changed_job_marks) filters by
-- workflow_key and updated_at > watermark on every poll pass.
create index if not exists idx_jobs_workflow_updated on jobs(workflow_key, updated_at);
-- Workflow worker periodic full rescan (list_active_job_marks, schema v35):
-- filters active rows of one workflow ordered by created_at desc. Partial so
-- terminal rows (the overwhelming majority on a busy instance) neither bloat
-- the index nor force a seq scan + sort of the whole jobs table every pass.
create index if not exists idx_jobs_active_marks
  on jobs(workflow_key, created_at desc)
  where status not in ('completed', 'failed');
create index if not exists idx_jobs_workflow_source on jobs(workflow_key, source_type, source_id);
create index if not exists idx_jobs_workspace_workflow_status on jobs(workspace_id, workflow_key, status);
create index if not exists idx_jobs_workspace_workflow_source on jobs(workspace_id, workflow_key, source_type, source_id);
-- Snapshot pagination (list_jobs_paginated) and the legacy unbounded job list
-- both filter by workspace and order by (created_at desc, id desc); without
-- this index every page re-sorts all jobs of the workspace.
create index if not exists idx_jobs_workspace_created_at on jobs(workspace_id, created_at desc, id desc);
create index if not exists idx_job_nodes_job_status on job_nodes(job_id, status);
create index if not exists idx_node_runs_job_id on node_runs(job_id);
-- get_latest_node_run_for_workspace orders by started_at desc with limit 1;
-- the index lets Postgres walk runs newest-first instead of sorting them all.
create index if not exists idx_node_runs_started_at on node_runs(started_at desc);
-- Cleanup sweep keyset pagination (schema v38): sweep_expired_node_runs
-- pages expired rows by (finished_at, id); the composite index serves the
-- id tie-break without rescanning rows that share a finished_at, and covers
-- the old two-column index as a leftmost prefix.
drop index if exists idx_node_runs_status_finished_at;
create index if not exists idx_node_runs_status_finished_at_id
  on node_runs(status, finished_at, id);
create index if not exists idx_jobs_status on jobs(status);
-- Workspace job status counters (schema v36, DB-JOB-STATUS-COUNTS-001):
-- count_jobs_by_status serves the event aggregator flush (every 0.5s per
-- dirty workspace), job list snapshots, intake and deletion broadcasts; as a
-- group-by over the whole workspace slice of jobs it is O(workspace jobs) per
-- call (0.3~1.1s measured at 130k rows under load). Row triggers keep this
-- table transactionally in sync so the read is a PK lookup of a few rows.
-- Backfill lives in migrate_workspace_job_status_counts.
create table if not exists workspace_job_status_counts (
  workspace_id text not null references workspaces(id) on delete cascade,
  status text not null,
  cnt bigint not null,
  primary key(workspace_id, status)
);
create or replace function sync_workspace_job_status_counts() returns trigger as $$
begin
  if TG_OP = 'INSERT' then
    insert into workspace_job_status_counts(workspace_id, status, cnt)
    values (NEW.workspace_id, NEW.status, 1)
    on conflict (workspace_id, status)
    do update set cnt = workspace_job_status_counts.cnt + 1;
    return NEW;
  elsif TG_OP = 'DELETE' then
    update workspace_job_status_counts set cnt = cnt - 1
    where workspace_id = OLD.workspace_id and status = OLD.status;
    return OLD;
  else
    if NEW.workspace_id is distinct from OLD.workspace_id
       or NEW.status is distinct from OLD.status then
      update workspace_job_status_counts set cnt = cnt - 1
      where workspace_id = OLD.workspace_id and status = OLD.status;
      insert into workspace_job_status_counts(workspace_id, status, cnt)
      values (NEW.workspace_id, NEW.status, 1)
      on conflict (workspace_id, status)
      do update set cnt = workspace_job_status_counts.cnt + 1;
    end if;
    return NEW;
  end if;
end;
$$ language plpgsql;
-- drop-then-create keeps the whole-file replay idempotent across upgrades.
drop trigger if exists jobs_status_counts_sync on jobs;
create trigger jobs_status_counts_sync
  after insert or delete or update of status, workspace_id on jobs
  for each row execute function sync_workspace_job_status_counts();
-- Workspace job NODE status counters (schema v56, DB-JOB-NODE-STATUS-COUNTS-001):
-- count_workspace_job_nodes_by_status serves the workspace DAG endpoint; as a
-- join+group-by over job_nodes ⋈ jobs it is O(workspace job_nodes) per call
-- (48s measured at 260k jobs / 2.9M job_nodes, hash join spilling ~1GB to
-- temp). Triggers keep this table transactionally in sync; job_nodes rows
-- derive (workspace_id, workflow_key) from their parent jobs row.
-- Backfill lives in migrate_workspace_job_node_status_counts.
create table if not exists workspace_job_node_status_counts (
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  status text not null,
  cnt bigint not null,
  primary key(workspace_id, workflow_key, node_key, status)
);
create or replace function bump_job_node_status_counts(
  p_workspace_id text, p_workflow_key text, p_node_key text, p_status text, p_delta bigint
) returns void as $$
begin
  if p_delta > 0 then
    insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
    values (p_workspace_id, p_workflow_key, p_node_key, p_status, p_delta)
    on conflict (workspace_id, workflow_key, node_key, status)
    do update set cnt = workspace_job_node_status_counts.cnt + p_delta;
  else
    update workspace_job_node_status_counts set cnt = cnt + p_delta
    where workspace_id = p_workspace_id and workflow_key = p_workflow_key
      and node_key = p_node_key and status = p_status;
  end if;
end;
$$ language plpgsql;
create or replace function sync_job_node_status_counts() returns trigger as $$
declare
  parent_ws text;
  parent_wf text;
begin
  if TG_OP = 'INSERT' then
    select workspace_id, workflow_key into parent_ws, parent_wf
      from jobs where id = NEW.job_id;
    if found then
      perform bump_job_node_status_counts(parent_ws, parent_wf, NEW.node_key, NEW.status, 1);
    end if;
    return NEW;
  elsif TG_OP = 'DELETE' then
    -- On cascade delete the parent jobs row is already gone (the jobs
    -- BEFORE DELETE trigger deducted this job's counts set-based); only a
    -- direct job_nodes delete finds the parent and decrements here.
    select workspace_id, workflow_key into parent_ws, parent_wf
      from jobs where id = OLD.job_id;
    if found then
      perform bump_job_node_status_counts(parent_ws, parent_wf, OLD.node_key, OLD.status, -1);
    end if;
    return OLD;
  else
    if NEW.job_id is distinct from OLD.job_id
       or NEW.node_key is distinct from OLD.node_key
       or NEW.status is distinct from OLD.status then
      select workspace_id, workflow_key into parent_ws, parent_wf
        from jobs where id = OLD.job_id;
      if found then
        perform bump_job_node_status_counts(parent_ws, parent_wf, OLD.node_key, OLD.status, -1);
      end if;
      select workspace_id, workflow_key into parent_ws, parent_wf
        from jobs where id = NEW.job_id;
      if found then
        perform bump_job_node_status_counts(parent_ws, parent_wf, NEW.node_key, NEW.status, 1);
      end if;
    end if;
    return NEW;
  end if;
end;
$$ language plpgsql;
-- Job deletion: deduct every node count set-based BEFORE the row goes away;
-- the cascaded job_nodes deletes afterwards find no parent and skip.
create or replace function deduct_job_node_status_counts() returns trigger as $$
begin
  update workspace_job_node_status_counts c set cnt = c.cnt - s.cnt
  from (
    select node_key, status, count(*) as cnt from job_nodes
    where job_id = OLD.id group by 1, 2
  ) s
  where c.workspace_id = OLD.workspace_id and c.workflow_key = OLD.workflow_key
    and c.node_key = s.node_key and c.status = s.status;
  return OLD;
end;
$$ language plpgsql;
-- Job re-key (workspace move / workflow_key change): move every node count.
create or replace function rekey_job_node_status_counts() returns trigger as $$
begin
  update workspace_job_node_status_counts c set cnt = c.cnt - s.cnt
  from (
    select node_key, status, count(*) as cnt from job_nodes
    where job_id = OLD.id group by 1, 2
  ) s
  where c.workspace_id = OLD.workspace_id and c.workflow_key = OLD.workflow_key
    and c.node_key = s.node_key and c.status = s.status;
  insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
  select NEW.workspace_id, NEW.workflow_key, node_key, status, count(*)
  from job_nodes where job_id = NEW.id group by 3, 4
  on conflict (workspace_id, workflow_key, node_key, status)
  do update set cnt = workspace_job_node_status_counts.cnt + excluded.cnt;
  return NEW;
end;
$$ language plpgsql;
-- drop-then-create keeps the whole-file replay idempotent across upgrades.
drop trigger if exists job_nodes_status_counts_sync on job_nodes;
create trigger job_nodes_status_counts_sync
  after insert or delete or update of status, node_key, job_id on job_nodes
  for each row execute function sync_job_node_status_counts();
drop trigger if exists jobs_node_status_counts_deduct on jobs;
create trigger jobs_node_status_counts_deduct
  before delete on jobs
  for each row execute function deduct_job_node_status_counts();
drop trigger if exists jobs_node_status_counts_rekey on jobs;
create trigger jobs_node_status_counts_rekey
  after update of workspace_id, workflow_key on jobs
  for each row execute function rekey_job_node_status_counts();
create index if not exists idx_executor_leases_global_active on executor_leases(executor_id, status, expires_at);
create index if not exists idx_executor_leases_workspace_active on executor_leases(workspace_id, executor_id, status, expires_at);
create index if not exists idx_executor_leases_workflow_node_active on executor_leases(workspace_id, workflow_key, node_key, status, expires_at);
create index if not exists idx_executor_leases_status_expires_at on executor_leases(status, expires_at);
create index if not exists idx_executor_leases_job_status on executor_leases(job_id, status);

drop table if exists remote_executions;
drop table if exists remote_workers;
create index if not exists idx_workspace_packages_workspace_id on workspace_packages(workspace_id, created_at desc);
create index if not exists idx_workflow_revisions_active on workflow_revisions(workspace_id, workflow_key, status);
create index if not exists idx_node_run_token_usage_workspace on node_run_token_usage(workspace_id, node_key);
create index if not exists idx_node_run_token_usage_model on node_run_token_usage(provider, model);
create index if not exists idx_node_run_token_usage_skill_version on node_run_token_usage(skill_version);
create index if not exists idx_node_runs_run_dir on node_runs(run_dir);
create index if not exists idx_node_run_token_usage_job_id on node_run_token_usage(job_id);
-- Ops metrics token samplers (schema v38): the minute sampler aggregates
-- node_run_token_usage by created_at range (global sum, per-worker join,
-- per-workspace group-by); without this index every minute bucket
-- seq-scans the whole table.
create index if not exists idx_node_run_token_usage_created_at on node_run_token_usage(created_at);
create index if not exists idx_artifact_refs_hash on artifact_refs(hash);

-- Failure classification (schema v9): persisted category/detail for failed
-- node runs, mirrored onto job_nodes so detail views read them directly.
alter table node_runs add column if not exists failure_category text not null default '';
alter table node_runs add column if not exists failure_detail text not null default '';
alter table job_nodes add column if not exists failure_category text not null default '';
alter table job_nodes add column if not exists failure_detail text not null default '';
create index if not exists idx_node_runs_failure on node_runs(status, failure_category);
-- Dispatch-time config audit (schema v49, CONFIG-RUNTIME-MUTABLE-001): the
-- non-secret resolved node config actually used for this run. Frozen keys
-- repeat the intake snapshot; runtime_mutable keys carry the dispatch-time
-- re-resolution, so a mid-job switch change stays auditable per attempt.
alter table node_runs add column if not exists config_snapshot_json text not null default '';
-- Ops metrics queue summary (schema v48, issue #106): the unclaimable_model
-- sweep counter filters job_nodes by failure_detail plus a finished_at
-- range; without an index every collection seq-scans the whole
-- (multi-million-row) table. Partial so only the tiny unclaimable slice is
-- indexed — finished rows overwhelmingly carry other failure_detail values
-- or none. Serves both the fleet count and the workspace-scoped variant
-- (the exists probe into jobs resolves by primary key per matching row).
create index if not exists idx_job_nodes_unclaimable_finished
  on job_nodes(finished_at)
  where failure_detail = 'unclaimable_model';

-- Ops metrics (schema v11): minute-granularity host operations samples
-- (online Workers, claimed executions, token throughput), rolled up to
-- hour/day granularity by the /api/metrics/overview query.
-- Schema v12 adds worker_id: '' is the global aggregate row, any other
-- value is a per-Worker sample for the same bucket. Uniqueness lives in
-- uq_ops_metric_samples_bucket_worker (not an inline constraint) so fresh
-- and v11-upgraded databases share the same shape for upsert inference.
-- Schema v22 adds queued: Agent execution queue depth, sampled on the global
-- row only (the queue is workspace-dimensioned, not Worker-dimensioned).
-- Schema v23 adds workspace_id: '' is the global/fleet row (or, with a
-- non-empty worker_id, the per-Worker row); a non-empty workspace_id marks a
-- per-workspace sample (queued/active/tokens) for workspace-scoped
-- monitoring. Uniqueness widens to (bucket_start, worker_id, workspace_id).
create table if not exists ops_metric_samples (
  id bigint generated by default as identity primary key,
  bucket_start timestamptz not null,
  worker_id text not null default '',
  workspace_id text not null default '',
  online_workers integer not null default 0,
  active_executions integer not null default 0,
  queued integer not null default 0,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cache_read_tokens bigint not null default 0,
  total_tokens bigint not null default 0,
  created_at timestamptz not null default current_timestamp
);
alter table ops_metric_samples add column if not exists worker_id text not null default '';
alter table ops_metric_samples add column if not exists workspace_id text not null default '';
alter table ops_metric_samples add column if not exists queued integer not null default 0;
alter table ops_metric_samples drop constraint if exists ops_metric_samples_bucket_start_key;
create index if not exists idx_ops_metric_samples_bucket on ops_metric_samples(bucket_start);
drop index if exists uq_ops_metric_samples_bucket_worker;
create unique index if not exists uq_ops_metric_samples_bucket_worker
  on ops_metric_samples(bucket_start, worker_id, workspace_id);

-- Agent queue signals (schema v22): single-row runtime signal written by the
-- empty-claim trigger when a claim comes back empty while queued rows remain
-- (blocked queue, issue #13). The monitoring summary reads it with a
-- freshness window, so rows self-expire and never need clearing.
create table if not exists agent_queue_signals (
  id integer primary key check (id = 1),
  kind text not null,
  reasons_json text not null default '{}',
  updated_at timestamptz not null default current_timestamp
);

-- Auth (schema v13): local users, revocable server-side sessions, and
-- per-workspace membership for the B-end self-hosted rollout. Session rows
-- store only the sha256 of the bearer token so a database leak does not
-- expose usable credentials (SECURITY-AUTH-001).
create table if not exists users (
  id text primary key,
  username text not null unique,
  display_name text not null default '',
  password_hash text,
  role text not null default 'member' check(role in ('admin', 'member')),
  disabled_at timestamptz,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
);

create table if not exists sessions (
  token_hash text primary key,
  user_id text not null references users(id) on delete cascade,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default current_timestamp
);
create index if not exists idx_sessions_user_id on sessions(user_id);
create index if not exists idx_sessions_expires_at on sessions(expires_at);

-- Scoped tokens (schema v41): short-lived bearer tokens minted by the server
-- for built-in agents (studio chat runs, STUDIO-AGENT-001). Each token binds
-- the initiating user and an actor_scope, carries a fixed TTL (no sliding
-- expiry), and is revocable. Like sessions, only the sha256 of the raw token
-- is persisted.
create table if not exists auth_scoped_tokens (
  -- Public, non-sensitive identifier for self-service management (v42); the
  -- token_hash digest is never exposed by the API.
  id text not null default gen_random_uuid()::text,
  token_hash text primary key,
  user_id text not null references users(id) on delete cascade,
  scope text not null,
  -- 'run' = minted per studio chat run (short TTL); 'user' = self-service
  -- token minted via /api/studio-agent-tokens for external agents (v42).
  origin text not null default 'run',
  -- Workspace binding for run tokens (schema v45): the studio-agent tool
  -- surface refuses workspace-path endpoints for any other workspace.
  -- NULL for self-service tokens, which keep the unbound behaviour.
  workspace_id text,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default current_timestamp
);
-- Upgrade path for pre-v42 databases: create table if not exists skips the
-- existing v41 table, so the columns must be added here before the unique
-- index below can be built on id.
alter table auth_scoped_tokens add column if not exists origin text not null default 'run';
alter table auth_scoped_tokens add column if not exists id text;
update auth_scoped_tokens set id = gen_random_uuid()::text where id is null;
alter table auth_scoped_tokens alter column id set not null;
alter table auth_scoped_tokens alter column id set default gen_random_uuid()::text;
-- Upgrade path for pre-v45 databases (workspace binding).
alter table auth_scoped_tokens add column if not exists workspace_id text;
create unique index if not exists idx_auth_scoped_tokens_id on auth_scoped_tokens(id);
create index if not exists idx_auth_scoped_tokens_user_id on auth_scoped_tokens(user_id);
create index if not exists idx_auth_scoped_tokens_expires_at on auth_scoped_tokens(expires_at);

create table if not exists workspace_members (
  workspace_id text not null references workspaces(id) on delete cascade,
  user_id text not null references users(id) on delete cascade,
  role text not null default 'editor' check(role in ('editor', 'viewer')),
  created_at timestamptz not null default current_timestamp,
  primary key (workspace_id, user_id)
);
create index if not exists idx_workspace_members_user_id on workspace_members(user_id);

-- Vault (schema v16): per-workspace secrets encrypted with the Fernet master
-- key (VAULT-SECRET-001). Only ciphertext is stored; plaintext never leaves
-- the vault service layer and is never returned by the API.
create table if not exists workspace_secrets (
  workspace_id text not null references workspaces(id) on delete cascade,
  name text not null,
  ciphertext text not null,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  primary key(workspace_id, name)
);

-- Custom node codes (schema v25): DB-backed custom workflow node code with
-- immutable versions (EXEC-CODE-002). The partial unique index guarantees at
-- most one published version per (workspace, workflow, node).
create table if not exists workflow_node_codes (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  node_key text not null,
  version integer not null,
  status text not null check(status in ('draft', 'published', 'archived')),
  code text not null,
  code_hash text not null,
  created_by text not null,
  change_note text,
  created_at timestamptz not null default current_timestamp,
  published_at timestamptz,
  unique(workspace_id, workflow_key, node_key, version)
);
create unique index if not exists workflow_node_codes_published
  on workflow_node_codes(workspace_id, workflow_key, node_key)
  where status = 'published';

-- Versioned entities (schema v26): unified draft → published → archived
-- lifecycle storage for custom node codes ('node_code') and Agent definitions
-- ('agent'; executor definitions joined at v30 and retired at v47).
-- New node-code and Agent entities are workspace-scoped. workspace_id may be
-- NULL only on legacy node-code rows retained for historical replay. NULLS
-- NOT DISTINCT keeps uniqueness meaningful for those rows (PostgreSQL 15+).
create table if not exists versioned_entities (
  id text primary key,
  entity_type text not null check(entity_type in ('node_code', 'agent')),
  workspace_id text references workspaces(id) on delete cascade,
  entity_key text not null,
  version integer not null,
  status text not null check(status in ('draft', 'published', 'archived')),
  definition_json text not null,
  definition_hash text not null,
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  published_at timestamptz,
  unique nulls not distinct(entity_type, workspace_id, entity_key, version)
);
create unique index if not exists versioned_entities_published
  on versioned_entities(entity_type, workspace_id, entity_key) nulls not distinct
  where status = 'published';
-- Capability uniqueness for published Agents, per workspace (schema v46):
-- workspace routes derive from the capability alone, so two published Agents
-- of one workspace sharing a capability would make routing ambiguous. The
-- service layer checks first; this partial index is the real guard. Agent
-- rows are always workspace-scoped (the v46 migration deleted the global
-- rows), so workspace_id is never NULL under this predicate.
create unique index if not exists versioned_entities_published_capability
  on versioned_entities(workspace_id, (definition_json::jsonb->>'capability'))
  where entity_type = 'agent' and status = 'published';
create index if not exists idx_versioned_entities_type_key
  on versioned_entities(entity_type, entity_key);

-- Quality loop (schema v28): deterministic sampling of node runs into review
-- batches, per-run snapshot items, and insert-only labels whose newest row
-- per (item_id, target) is the current label.
create table if not exists quality_sample_batches (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  name text not null default '',
  workflow_key text not null default '',
  filters_json jsonb not null default '{}',
  sample_size integer not null check(sample_size > 0),
  seed text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp
);
create index if not exists idx_quality_sample_batches_workspace
  on quality_sample_batches(workspace_id, created_at desc);

-- Item rows are point-in-time snapshots: node_run_id/job_id deliberately
-- carry no FK so the sample stays analyzable after run cleanup.
create table if not exists quality_sample_items (
  id text primary key,
  batch_id text not null references quality_sample_batches(id) on delete cascade,
  node_run_id bigint not null,
  job_id text not null,
  node_key text not null default '',
  capability text not null default '',
  skill_version text not null default '',
  agent_definition_hash text not null default '',
  agent_version integer,
  provider text not null default '',
  model text not null default '',
  run_status text not null default '',
  failure_category text not null default '',
  failure_detail text not null default '',
  created_at timestamptz not null default current_timestamp,
  unique(batch_id, node_run_id)
);
create index if not exists idx_quality_sample_items_batch
  on quality_sample_items(batch_id);

create table if not exists quality_labels (
  id text primary key,
  item_id text not null references quality_sample_items(id) on delete cascade,
  target text not null check(target in ('run', 'replay')),
  verdict text not null check(verdict in ('good', 'bad')),
  reason_codes jsonb not null default '[]',
  note text not null default '',
  labeled_by text not null default '',
  created_at timestamptz not null default current_timestamp
);
create index if not exists idx_quality_labels_item_target
  on quality_labels(item_id, target, created_at desc);

-- Quality replays (schema v29): a replay re-runs one sampled node inside an
-- isolated copy job built from the original job's frozen workflow snapshot —
-- upstream inputs are file-copied into the copy's job directory, upstream
-- nodes are marked completed, downstream nodes not_applicable, so only the
-- target node is scheduled and the original job is never touched. Status is
-- reconciled lazily from the copy job's node row on read.
create table if not exists quality_replays (
  id text primary key,
  item_id text not null references quality_sample_items(id) on delete cascade,
  agent_id text not null default '',
  agent_version integer,
  replay_job_id text not null default '',
  status text not null default 'pending'
    check(status in ('pending', 'running', 'succeeded', 'failed')),
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  finished_at timestamptz
);
create index if not exists idx_quality_replays_item
  on quality_replays(item_id, created_at desc);
-- At most one in-flight replay per sample item; the service reconciles lazy
-- terminal states before evaluating this guard, the index is the backstop.
create unique index if not exists quality_replays_one_active_per_item
  on quality_replays(item_id) where status in ('pending', 'running');

-- Replay labels must distinguish multiple replays of the same item:
-- latest-wins groups by (item_id, target, replay_id).
alter table quality_labels add column if not exists replay_id text;

-- Per-run Agent version pin (quality replay): when set, the broker enqueue
-- check, the claim candidate join, and the definition sweepers match this
-- immutable version row instead of the currently published one.
alter table agent_execution_requests add column if not exists pinned_agent_version integer;

-- Instance-level external service connections (schema v34): admin-managed
-- auth integrations (e.g. CMS) shared across workspaces. config_json carries
-- non-sensitive fields plus {"secret_ref": name} markers; secrets live in
-- instance_secrets (Fernet-encrypted, VAULT-SECRET-001).
create table if not exists external_connections (
  key text primary key,
  type text not null,
  display_name text not null default '',
  config_json text not null default '{}',
  enabled integer not null default 1,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
);

-- Instance-scope vault (schema v34): same Fernet semantics as
-- workspace_secrets but not bound to any workspace.
create table if not exists instance_secrets (
  name text primary key,
  ciphertext text not null,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
);

-- Acquired connection tokens (schema v34): the global runtime token store.
-- Refresh is single-flight via a row lock on the parent connection row.
create table if not exists connection_tokens (
  connection_key text primary key references external_connections(key) on delete cascade,
  token_ciphertext text not null,
  expires_at timestamptz,
  refreshed_at timestamptz not null default current_timestamp
);

-- Studio chat (schema v43, phase 3 chunk 4; v45 adds selected_node_key and
-- the 'thought' message kind): ACP conversation backend. One row
-- per Studio conversation session (an in-process handle onto an ACP agent
-- subprocess) plus the persisted message timeline. capability_snapshot_json
-- freezes the capabilities negotiated at ACP initialize; mcp_status is the
-- behavioural smoke signal for agent-legion MCP tool visibility. For pre-v43
-- databases these create-if-not-exists statements are the upgrade path (the
-- tables simply do not exist yet); migrations/studio_chat.py replays the same
-- DDL as the idempotent fallback.
create table if not exists studio_chat_sessions (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  user_id text not null references users(id) on delete cascade,
  agent_id text not null,
  title text not null default '',
  status text not null default 'starting'
    check(status in ('starting', 'idle', 'running', 'awaiting_permission',
                     'closed', 'error')),
  acp_session_id text,
  capability_snapshot_json text not null default '{}',
  allow_all_permissions boolean not null default false,
  mcp_status text not null default 'unknown'
    check(mcp_status in ('unknown', 'verified', 'unverified')),
  -- The node the human currently has selected in Studio (v45); pushed by the
  -- frontend, read live by the get_studio_context MCP tool.
  selected_node_key text,
  error_detail text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  closed_at timestamptz
);
create index if not exists idx_studio_chat_sessions_workspace
  on studio_chat_sessions(workspace_id, created_at desc);
-- Upgrade path for pre-v45 databases.
alter table studio_chat_sessions add column if not exists selected_node_key text;

create table if not exists studio_chat_messages (
  id text primary key,
  seq bigint generated always as identity,
  session_id text not null references studio_chat_sessions(id) on delete cascade,
  kind text not null
    check(kind in ('text', 'tool_call', 'plan', 'permission', 'status', 'thought')),
  role text not null check(role in ('user', 'agent', 'system')),
  content_json text not null default '{}',
  created_at timestamptz not null default current_timestamp
);
-- Upgrade path for pre-v45 databases: widen the kind check with 'thought'.
alter table studio_chat_messages drop constraint if exists studio_chat_messages_kind_check;
alter table studio_chat_messages add constraint studio_chat_messages_kind_check
  check(kind in ('text', 'tool_call', 'plan', 'permission', 'status', 'thought'));
create unique index if not exists idx_studio_chat_messages_seq
  on studio_chat_messages(seq);
create index if not exists idx_studio_chat_messages_session
  on studio_chat_messages(session_id, seq);

-- Materials (schema v52, materials-and-runs design §5.1): browser-uploaded
-- files. Bytes live in the instance S3-compatible object store under
-- storage_key; this row is the metadata. content_hash is '' when the client
-- did not compute one, so the dedup uniqueness is a partial unique index
-- over declared hashes only.
create table if not exists materials (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  content_hash text not null default '',
  filename text not null default '',
  content_type text not null default '',
  size_bytes bigint not null default 0,
  storage_key text not null,
  status text not null default 'uploading'
    check(status in ('uploading', 'ready', 'failed', 'expired')),
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  expires_at timestamptz
);
create unique index if not exists idx_materials_workspace_content_hash
  on materials(workspace_id, content_hash) where content_hash <> '';
create index if not exists idx_materials_workspace_created
  on materials(workspace_id, created_at desc);

-- Material bundles (schema v55, materials-and-runs design §5, #156): a
-- folder uploaded as one run item. A bundle owns no bytes — it is a manifest
-- of ready materials plus their relative paths; materialization rebuilds the
-- directory tree from the content-addressed cache. Members reference
-- materials(id) (no on delete cascade: the materials delete guard rejects
-- deleting a referenced member instead).
create table if not exists material_bundles (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  name text not null default '',
  total_size_bytes bigint not null default 0,
  file_count integer not null default 0,
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp
);
create index if not exists idx_material_bundles_workspace_created
  on material_bundles(workspace_id, created_at desc);

create table if not exists material_bundle_members (
  bundle_id text not null references material_bundles(id) on delete cascade,
  material_id text not null references materials(id),
  path text not null,
  ordinal integer not null,
  primary key (bundle_id, ordinal),
  unique (bundle_id, path)
);
create index if not exists idx_material_bundle_members_material
  on material_bundle_members(material_id);
