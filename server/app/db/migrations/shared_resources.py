"""Schema v107: immutable shared resources, grants, selections and job pins.

The migration owns the complete DDL so fresh and upgraded databases take the
same path. Published versions and their file manifests are immutable at the
database layer; logical deletion only sets ``deleted_at`` after the service's
reference guard. Objects use the separate ``shared-resources/`` prefix and are
never removed by this migration or the resource lifecycle API.
"""

from __future__ import annotations

from typing import Any

_DDL = """
create table if not exists shared_resources (
  id text primary key,
  owner_workspace_id text not null,
  name text not null,
  create_token text not null,
  create_request_hash text not null,
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  unique (owner_workspace_id, create_token)
);

create table if not exists shared_resource_versions (
  id text primary key,
  resource_id text not null references shared_resources(id) on delete restrict,
  version integer not null,
  status text not null check(status in ('copying', 'ready', 'failed')),
  source_type text not null check(source_type in ('material', 'bundle')),
  source_id text not null,
  source_workspace_id text not null,
  source_snapshot_json text not null,
  manifest_hash text not null,
  idempotency_key text not null,
  request_hash text not null,
  copy_generation integer not null default 0,
  failure_code text not null default '',
  failure_detail text not null default '',
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  ready_at timestamptz,
  deleted_at timestamptz,
  unique (resource_id, version),
  unique (resource_id, idempotency_key)
);
create index if not exists idx_shared_resource_versions_visible
  on shared_resource_versions(resource_id, version desc)
  where status = 'ready' and deleted_at is null;

create table if not exists shared_resource_files (
  id text primary key,
  version_id text not null references shared_resource_versions(id) on delete restrict,
  ordinal integer not null,
  path text not null,
  sha256 text not null,
  size_bytes bigint not null,
  content_type text not null default '',
  storage_key text not null,
  source_storage_key text not null,
  source_material_id text not null,
  source_upload_generation integer not null,
  source_etag text not null,
  unique (version_id, path),
  unique (version_id, storage_key),
  unique (version_id, ordinal)
);

create table if not exists shared_resource_grants (
  id text primary key,
  resource_id text not null references shared_resources(id) on delete restrict,
  workspace_id text not null references workspaces(id) on delete cascade,
  status text not null check(status in ('active', 'revoked')),
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  revoked_by text not null default '',
  revoked_at timestamptz,
  unique (resource_id, workspace_id)
);
create index if not exists idx_shared_resource_grants_workspace
  on shared_resource_grants(workspace_id, status, resource_id);

create table if not exists shared_resource_selections (
  grant_id text not null references shared_resource_grants(id) on delete cascade,
  workspace_id text not null,
  resource_id text not null,
  version_id text not null references shared_resource_versions(id) on delete restrict,
  revision integer not null,
  selected_by text not null,
  updated_at timestamptz not null default current_timestamp,
  primary key (workspace_id, resource_id),
  unique (grant_id)
);
create index if not exists idx_shared_resource_selections_version
  on shared_resource_selections(version_id);

create table if not exists shared_resource_job_pins (
  job_id text not null references jobs(id) on delete cascade,
  resource_id text not null,
  version_id text not null references shared_resource_versions(id) on delete restrict,
  manifest_hash text not null,
  grant_id text not null,
  created_at timestamptz not null default current_timestamp,
  primary key (job_id, resource_id)
);
create index if not exists idx_shared_resource_job_pins_version
  on shared_resource_job_pins(version_id);

create or replace function shared_resource_ready_version_immutable()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    if old.status = 'ready' then
      raise exception 'ready shared resource versions are immutable';
    end if;
    return old;
  end if;
  if old.status = 'ready'
    and (to_jsonb(new) - 'deleted_at') is distinct from (to_jsonb(old) - 'deleted_at')
  then
    raise exception 'ready shared resource versions are immutable';
  end if;
  return new;
end;
$$;

create or replace function shared_resource_ready_files_immutable()
returns trigger language plpgsql as $$
declare
  pinned_version_id text;
begin
  pinned_version_id := case when tg_op = 'INSERT' then new.version_id else old.version_id end;
  if exists (
    select 1 from shared_resource_versions
    where id = pinned_version_id and status = 'ready'
  ) then
    raise exception 'files of a ready shared resource version are immutable';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

drop trigger if exists shared_resource_versions_immutable on shared_resource_versions;
create trigger shared_resource_versions_immutable
  before update or delete on shared_resource_versions
  for each row execute function shared_resource_ready_version_immutable();

drop trigger if exists shared_resource_files_immutable on shared_resource_files;
create trigger shared_resource_files_immutable
  before insert or update or delete on shared_resource_files
  for each row execute function shared_resource_ready_files_immutable();
"""


def migrate_shared_resources(conn: Any) -> None:
    """Create the shared-resource schema (v107); idempotent on replay."""
    conn.execute(_DDL)
