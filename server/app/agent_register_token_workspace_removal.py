"""Workspace-level register-key removal (delete_workspace 的级联入口).

Split from agent_register_token_deletion.py for the file-size budget: the
per-key cascade is reused verbatim, this module only adds the loop over one
workspace's keys inside the caller's (delete_workspace) transaction.
"""

from __future__ import annotations

from typing import Any

from server.app.agent_register_token_deletion import cascade_delete_register_token


def cascade_delete_workspace_register_tokens(conn: Any, workspace_id: str) -> list[str]:
    """Delete every register token of one workspace inside `conn`'s open
    transaction, running the per-token Worker cascade for each.

    Called from delete_workspace BEFORE the workspaces row goes away: after
    that the FK on delete cascade would drop the keys silently and Worker
    rows would keep a scope entry for the dead workspace id — and a
    same-name recreation reuses the slug id, instantly re-admitting those
    stale Workers. Returns the aggregate cascade-deleted worker_ids."""
    deleted: list[str] = []
    token_rows = conn.execute(
        "select id from agent_register_tokens where workspace_id=%s order by id",
        (workspace_id,),
    ).fetchall()
    for token_row in token_rows:
        per_token = cascade_delete_register_token(conn, str(token_row["id"]))
        if per_token:
            deleted.extend(per_token)
    return deleted
