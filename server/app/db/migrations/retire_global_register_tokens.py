"""Schema v58: retire all-workspaces register tokens (issue #35).

The global worker register token and the workspace_id IS NULL variant of
scoped register tokens were retired together: registration is only possible
with a workspace-scoped token now. Any legacy all-workspaces token still
marked live would silently keep admitting workers to every workspace —
migrate them to revoked and let the operator re-issue per-workspace tokens.
Idempotent on replay: only still-live NULL-workspace rows are touched.
"""

from __future__ import annotations

from typing import Any

_RETIRED_TOKENS_DDL = """
update agent_register_tokens
   set revoked_at = current_timestamp
 where workspace_id is null
   and revoked_at is null
"""


def migrate_retire_global_register_tokens(conn: Any) -> None:
    """Revoke legacy all-workspaces register tokens (issue #35, v58)."""
    conn.execute(_RETIRED_TOKENS_DDL)
