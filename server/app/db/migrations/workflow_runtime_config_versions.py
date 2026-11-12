"""Schema v102 (M47): append-only agent runtime config version history.

``workflow_runtime_config_versions`` records every runtime-channel save as an
immutable audit row: the runtime slice (per-node provider/model/thinking/
prompt/prompt_mode/tools/requires_labels + the top-level execution defaults
of the same key family) that the save wrote into the active revision, the
changed keys, and the operator. Dispatch still resolves the active revision's
definition_json — the table is the history/rollback source, not a resolution
input; in-flight jobs keep their intake-frozen snapshots untouched. Rollback
appends a NEW version whose content is the target version's slice applied to
the current structure — nodes the target does not know keep their current
values, so after an intervening structural change the result is not the
target verbatim (history is never rewritten).

This module owns the table's DDL — postgres_schema.sql does not create it
(the schema file sits at its absolute line ceiling; fresh and pre-v102
databases both run this apply fn — the parity test pins the shapes equal).
The ``(workspace_id, version)`` unique constraint serializes concurrent
allocations; writers hold the implementation-publication lock, so the max+1
read inside the same transaction cannot race.
"""

from __future__ import annotations

from typing import Any

_RUNTIME_CONFIG_VERSIONS_DDL = """
create table if not exists workflow_runtime_config_versions (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  revision_id text not null,
  version integer not null,
  config_json text not null,
  changed_keys_json text not null,
  created_by text not null,
  created_at timestamp not null default current_timestamp
)
"""

_RUNTIME_CONFIG_VERSIONS_UNIQUE = """
create unique index if not exists workflow_runtime_config_versions_workspace_version
  on workflow_runtime_config_versions(workspace_id, version)
"""


def migrate_workflow_runtime_config_versions(conn: Any) -> None:
    """Create the runtime config version table (v102); idempotent on replay."""
    conn.execute(_RUNTIME_CONFIG_VERSIONS_DDL)
    conn.execute(_RUNTIME_CONFIG_VERSIONS_UNIQUE)
