"""workspace_secrets vault table (schema v16); idempotent on replay.

Moved from ``db/schema.py`` (P-0.5) to keep that module within its size
budget; the architecture gate restricts schema mutations to the db module
or this migrations package.
"""

from __future__ import annotations

from typing import Any

_WORKSPACE_SECRETS_DDL = """
create table if not exists workspace_secrets (
  workspace_id text not null references workspaces(id) on delete cascade,
  name text not null,
  ciphertext text not null,
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  primary key(workspace_id, name)
)
"""


def migrate_workspace_secrets(conn: Any) -> None:
    # Create the workspace_secrets vault table (v16); idempotent on replay.
    conn.execute(_WORKSPACE_SECRETS_DDL)
