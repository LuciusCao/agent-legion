"""Schema v42: auth_scoped_tokens gains origin and a public id column."""

from __future__ import annotations

from typing import Any

# Scoped-token self-service (schema v42): origin distinguishes tokens minted
# per studio chat run ('run', short TTL) from user-minted tokens for external
# agents ('user', /api/studio-agent-tokens). The id column is the public,
# non-sensitive identifier the management API lists and revokes by — the
# token_hash digest never leaves the server. Idempotent on replay: the column
# adds are guarded by IF NOT EXISTS and the backfill only touches NULL ids.
_SCOPED_TOKEN_ORIGIN_DDL = """
alter table auth_scoped_tokens add column if not exists origin text not null default 'run';
alter table auth_scoped_tokens add column if not exists id text;
update auth_scoped_tokens set id = gen_random_uuid()::text where id is null;
alter table auth_scoped_tokens alter column id set not null;
alter table auth_scoped_tokens alter column id set default gen_random_uuid()::text;
create unique index if not exists idx_auth_scoped_tokens_id on auth_scoped_tokens(id)
"""


def migrate_scoped_token_origin(conn: Any) -> None:
    """Add origin/id to auth_scoped_tokens and backfill ids (v42)."""
    conn.execute(_SCOPED_TOKEN_ORIGIN_DDL)
