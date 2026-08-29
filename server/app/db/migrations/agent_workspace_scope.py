"""Agent definitions become workspace-scoped (schema v46).

Before v46 every Agent definition was global (``workspace_id IS NULL``) and
capability uniqueness was enforced globally by the partial unique index
``versioned_entities_published_capability``. This migration:

1. Drops the legacy global capability index FIRST: on an upgrade (v45 → v46)
   the old ``versioned_entities_published_capability`` (keyed by capability
   alone) still exists, and the copies below share capabilities with the
   not-yet-deleted global rows — the copy would hit a UniqueViolation.
2. Copies the globally published Agent definition of every capability a
   workspace actually references (workflow revision nodes + materialized
   node routes) into that workspace as version 1 (new row id, same
   ``entity_key``/``definition_json``/``definition_hash``).
3. Deletes every global Agent row (any status — no global archive is kept).
4. Creates the per-workspace capability index
   ``(workspace_id, definition_json->>'capability')``.

Idempotent on replay: the drop is if-exists, copies are guarded by NOT
EXISTS, the delete affects zero rows on a second run, and the index create
is if-not-exists. The new index is created only after the global rows are
gone so no workspace_id NULL agent row ever enters it.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.db.migrations.agent_workspace_scope_pinned import (
    warn_pinned_versions_left_behind,
)

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

# Per-workspace capability uniqueness (was global before v46). The legacy
# index must drop BEFORE the copy: on upgrades it keys capability alone, so
# the workspace copies would collide with the still-present global rows.
_DROP_LEGACY_INDEX = "drop index if exists versioned_entities_published_capability"
_CREATE_WORKSPACE_INDEX = """
create unique index if not exists versioned_entities_published_capability
  on versioned_entities(workspace_id, (definition_json::jsonb->>'capability'))
  where entity_type = 'agent' and status = 'published'
"""


def migrate_agent_workspace_scope(conn: Any) -> None:
    """Drop the global index, copy referenced Agents per workspace, delete globals."""
    conn.execute(_DROP_LEGACY_INDEX)
    conn.execute(_COPY_REFERENCED)
    orphans = conn.execute(_UNRESOLVABLE_REFERENCES).fetchall()
    for row in orphans:
        logger.warning(
            "agent workspace scope migration: workspace %s references capability %r "
            "with no published global Agent definition; skipped (create one in Studio)",
            row["workspace_id"],
            row["capability"],
        )
    warn_pinned_versions_left_behind(conn)
    conn.execute(_DELETE_GLOBAL_AGENTS)
    conn.execute(_CREATE_WORKSPACE_INDEX)
