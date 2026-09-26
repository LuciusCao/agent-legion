"""Schema v84 (#626): workspace-scoped API intake tokens.

``workspace_api_tokens`` holds the machine-to-machine intake credentials:
an external system (CMS / form / cron / other agent) presents one via
``Authorization: Bearer {token_id}.{secret}`` against
``POST /api/workspaces/{id}/runs`` and the run/job read endpoints of the
same router — nothing else. The permission set is deliberately the minimal
editor subset (submit items + read status); the token can never reach admin
endpoints (``require_admin`` refuses any non-empty actor_scope), mint
further credentials, or edit workflow definitions, and the runs router
mounts ``require_workspace_api_intake`` instead of
``reject_studio_agent_scope`` so the studio-agent scope stays refused there
while the api scope is admitted (any other scope type keeps getting 403).

Aligned with agent_register_tokens (EXEC-WORKERACL-001): only the sha256
digest of the secret is stored; the plaintext ``{token_id}.{secret}`` is
returned exactly once at issuance. The table adds the lifecycle columns the
register tokens never needed: ``expires_at`` (optional TTL),
``revoked_at`` (soft revoke, unlike the register token's hard delete) and
``last_used_at`` (throttled usage watermark for the admin panel).

This module owns the table's DDL — postgres_schema.sql sits at its budget
ceiling (the v76 studio_publish_requests precedent): fresh and pre-v84
databases both run this apply fn, and the parity test pins the shapes equal.
"""

from __future__ import annotations

from typing import Any

_WORKSPACE_API_TOKENS_DDL = """
create table if not exists workspace_api_tokens (
  id text primary key,
  token_hash text not null,
  workspace_id text not null references workspaces(id) on delete cascade,
  label text not null default '',
  created_at timestamptz not null default current_timestamp,
  expires_at timestamptz,
  revoked_at timestamptz,
  last_used_at timestamptz
);
create index if not exists idx_workspace_api_tokens_workspace
  on workspace_api_tokens(workspace_id, created_at);
"""


def migrate_workspace_api_tokens(conn: Any) -> None:
    """Create the workspace API intake token table (v84, #626); idempotent."""
    conn.execute(_WORKSPACE_API_TOKENS_DDL)
