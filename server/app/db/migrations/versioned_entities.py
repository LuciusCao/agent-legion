"""Data migration applied alongside the idempotent DDL replay (v26)."""

from __future__ import annotations

from typing import Any

# Versioned entities (schema v26): one table backing the draft → published →
# archived lifecycle of both custom node codes ('node_code') and Agent
# definitions ('agent'). workspace_id is NULL for global entities (agents);
# node codes stay workspace-scoped. NULLS NOT DISTINCT makes the uniqueness
# guarantees hold for NULL workspace_id rows as well (PostgreSQL 15+).
# Idempotent on replay: the data copies are guarded by NOT EXISTS.
_VERSIONED_ENTITIES_DDL = """
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
-- Capability uniqueness for published Agents (agent config governance):
-- workspace routes derive from the capability alone, so two published Agents
-- sharing one capability would make routing ambiguous. The service layer
-- checks first; this partial index is the real guard. Re-publish stays legal
-- because publish archives the old row before promoting the draft in one
-- transaction.
create unique index if not exists versioned_entities_published_capability
  on versioned_entities((definition_json::jsonb->>'capability'))
  where entity_type = 'agent' and status = 'published';
create index if not exists idx_versioned_entities_type_key
  on versioned_entities(entity_type, entity_key)
"""

# Workspace-level Agent execution defaults (schema v26): provider/model/
# thinking resolved when a workflow node does not override them. Empty string
# means unset — the strict manifest guard rejects dispatch without a resolved
# provider/model. No global fallback exists by design.
_WORKSPACE_AGENT_DEFAULTS_DDL = """
alter table workspaces add column if not exists default_agent_provider text not null default '';
alter table workspaces add column if not exists default_agent_model text not null default '';
alter table workspaces add column if not exists default_agent_thinking text not null default ''
"""

_NODE_CODE_COPY = """
insert into versioned_entities(
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by, created_at, published_at
)
select
  n.id, 'node_code', n.workspace_id, n.workflow_key || ':' || n.node_key,
  n.version, n.status,
  json_build_object('code', n.code, 'change_note', n.change_note)::text,
  n.code_hash, n.created_by, n.created_at, n.published_at
from workflow_node_codes n
where not exists (
  select 1 from versioned_entities v
  where v.entity_type = 'node_code'
    and v.workspace_id is not distinct from n.workspace_id
    and v.entity_key = n.workflow_key || ':' || n.node_key
    and v.version = n.version
)
"""

_AGENT_COPY = """
insert into versioned_entities(
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by, created_at, published_at
)
select
  'agent:' || a.agent_id || ':v1', 'agent', null, a.agent_id, 1, 'published',
  a.definition_json, a.definition_hash, 'system', a.updated_at, a.updated_at
from agent_definitions a
where a.enabled = 1
  and not exists (
    select 1 from versioned_entities v
    where v.entity_type = 'agent'
      and v.workspace_id is null
      and v.entity_key = a.agent_id
      and v.version = 1
  )
"""


def migrate_versioned_entities(conn: Any) -> None:
    """Create versioned_entities + workspace Agent defaults; copy rows (v26).

    Copies existing workflow_node_codes and enabled agent_definitions rows
    into the unified table. The legacy agent_definitions copy is skipped when
    the table is already gone (fresh v27+ databases never create it; the v27
    cutover drops it everywhere else).
    """
    conn.execute(_VERSIONED_ENTITIES_DDL)
    conn.execute(_WORKSPACE_AGENT_DEFAULTS_DDL)
    conn.execute(_NODE_CODE_COPY)
    legacy = conn.execute("select to_regclass('agent_definitions') as t").fetchone()
    if legacy is not None and legacy["t"] is not None:
        conn.execute(_AGENT_COPY)
