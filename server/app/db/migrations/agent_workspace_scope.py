"""Agent definitions become workspace-scoped (schema v46).

Before v46 every Agent definition was global (``workspace_id IS NULL``) and
capability uniqueness was enforced globally by the partial unique index
``versioned_entities_published_capability``. This migration:

1. Copies the globally published Agent definition of every capability a
   workspace actually references (workflow revision nodes + materialized
   node routes) into that workspace as version 1 (new row id, same
   ``entity_key``/``definition_json``/``definition_hash``).
2. Deletes every global Agent row (any status — no global archive is kept).
3. Replaces the capability index with the per-workspace variant
   ``(workspace_id, definition_json->>'capability')``.

Idempotent on replay: copies are guarded by NOT EXISTS, the delete affects
zero rows on a second run, and the index swap is drop-if-exists +
create-if-not-exists. Copying must finish before the index swap so the old
global rows (NULL workspace_id) never collide with the new workspace rows.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Seed-if-absent per (workspace, capability): a workspace that already has a
# row for the entity key is left untouched (admin edits are never clobbered,
# replay is a no-op).
_COPY_REFERENCED = """
insert into versioned_entities(
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by, created_at, published_at
)
select
  'agent:' || ref.workspace_id || ':' || d.entity_key || ':v1',
  'agent', ref.workspace_id, d.entity_key, 1, 'published',
  d.definition_json, d.definition_hash, 'migration:v46',
  current_timestamp, current_timestamp
from (
  select wr.workspace_id, node.value->>'capability' as capability
  from workflow_revisions wr
  cross join lateral jsonb_each(wr.definition_json::jsonb->'nodes') node
  union
  select r.workspace_id, d2.definition_json::jsonb->>'capability' as capability
  from workspace_node_routes r
  join versioned_entities d2
    on d2.entity_type='agent' and d2.workspace_id is null
   and d2.entity_key = r.target_id and d2.status='published'
  where r.target_kind = 'agent'
) ref
join versioned_entities d
  on d.entity_type='agent' and d.workspace_id is null and d.status='published'
 and d.definition_json::jsonb->>'capability' = ref.capability
where ref.capability is not null and ref.capability <> ''
  and not exists (
    select 1 from versioned_entities v
    where v.entity_type='agent' and v.workspace_id = ref.workspace_id
      and v.entity_key = d.entity_key
  )
"""

# Capabilities referenced by revisions that matched nothing (post-copy check
# runs before the global delete): neither a fresh workspace copy nor a
# pre-existing workspace row covers them.
_UNRESOLVABLE_REFERENCES = """
select distinct wr.workspace_id, node.value->>'capability' as capability
from workflow_revisions wr
cross join lateral jsonb_each(wr.definition_json::jsonb->'nodes') node
where node.value->>'capability' is not null and node.value->>'capability' <> ''
  and not exists (
    select 1 from versioned_entities v
    where v.entity_type='agent' and v.workspace_id = wr.workspace_id
      and v.definition_json::jsonb->>'capability' = node.value->>'capability'
  )
"""

_DELETE_GLOBAL_AGENTS = (
    "delete from versioned_entities where entity_type='agent' and workspace_id is null"
)

# Per-workspace capability uniqueness (was global before v46).
_INDEX_SWAP = """
drop index if exists versioned_entities_published_capability;
create unique index if not exists versioned_entities_published_capability
  on versioned_entities(workspace_id, (definition_json::jsonb->>'capability'))
  where entity_type = 'agent' and status = 'published'
"""


def migrate_agent_workspace_scope(conn: Any) -> None:
    """Copy referenced global Agents into each workspace, drop the global rows."""
    conn.execute(_COPY_REFERENCED)
    orphans = conn.execute(_UNRESOLVABLE_REFERENCES).fetchall()
    for row in orphans:
        logger.warning(
            "agent workspace scope migration: workspace %s references capability %r "
            "with no published global Agent definition; skipped (create one in Studio)",
            row["workspace_id"],
            row["capability"],
        )
    conn.execute(_DELETE_GLOBAL_AGENTS)
    for statement in _INDEX_SWAP.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
