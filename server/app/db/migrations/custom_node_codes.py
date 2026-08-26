"""Create the workflow_node_codes table; retired from the registry chain.

Introduced in the v25 era, but the per-version registry
(``migration_registry.py``) has no entry for it: the same DDL lives in
``postgres_schema.sql`` and is replayed by every ``init_db``. The module is
kept so its pin test (tests/db/test_custom_node_codes_migration.py) can
still exercise the historical contract.
"""

from __future__ import annotations

from typing import Any

# Custom node codes (v25 era): DB-backed custom workflow node code with
# immutable versions and a draft → published → archived lifecycle
# (EXEC-CODE-002). The partial unique index guarantees at most one published
# version per (workspace, workflow, node). Idempotent on replay.
_WORKFLOW_NODE_CODES_DDL = """
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
  where status = 'published'
"""


def migrate_custom_node_codes(conn: Any) -> None:
    """Create the workflow_node_codes table; idempotent on replay."""
    conn.execute(_WORKFLOW_NODE_CODES_DDL)
