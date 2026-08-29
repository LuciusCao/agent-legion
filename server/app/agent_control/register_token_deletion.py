"""Cascading register-token deletion (key 即 scope 的切断语义).

Split from agent_register_tokens.py for the file-size budget: deleting a key
touches both credential tables, so the transaction lives in its own module.
"""

from __future__ import annotations

import json

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction


def delete_register_token_cascading(database_dsn: ConnectSource, token_id: str) -> list[str] | None:
    """Hard-delete a register token and cascade-cut dependent Workers.

    A Worker whose bound keys are ALL gone after this deletion loses its
    registration record in the same transaction — its worker_token dies
    immediately, otherwise a Worker that never re-registers would keep
    claiming forever on the stale credential. A Worker with other live keys
    keeps its record, but the dead binding is pruned and its stored scope is
    narrowed to what the surviving keys open, all atomically. Workers without
    a recorded binding (legacy pre-v59 registrations) are never touched.
    Returns the cascade-deleted worker_ids, or None when the token does not
    exist."""
    with write_transaction(database_dsn) as conn:
        return cascade_delete_register_token(conn, token_id)


def cascade_delete_register_token(conn, token_id: str) -> list[str] | None:
    """Run the cascade for one token inside `conn`'s open transaction.

    Shared by the admin DELETE route and delete_workspace: the FK on
    agent_register_tokens.workspace_id would otherwise silently drop the keys
    without this cascade, leaving Worker rows with a stale scope for a
    workspace id that a same-name recreation would hand to a NEW workspace —
    the whole point of delete-key-cuts-access."""
    result = conn.execute(
        "delete from agent_register_tokens where id=%s",
        (token_id,),
    )
    if result.rowcount == 0:
        return None
    rows = conn.execute(
        "select worker_id, register_token_ids_json from agent_workers"
        " where register_token_ids_json::jsonb @> jsonb_build_array(%s::text)"
        " order by worker_id for update",
        (token_id,),
    ).fetchall()
    deleted: list[str] = []
    for row in rows:
        remaining = [
            bound
            for bound in json.loads(row["register_token_ids_json"] or "[]")
            if bound != token_id
        ]
        live = (
            [
                token_row["id"]
                for token_row in conn.execute(
                    # revoked_at rows (legacy v58 revoke leftovers) do not
                    # count as live: a key that no longer admits
                    # registrations must not keep a Worker's record alive.
                    "select id from agent_register_tokens"
                    " where id = any(%s) and revoked_at is null",
                    (remaining,),
                ).fetchall()
            ]
            if remaining
            else []
        )
        if not live:
            conn.execute(
                "delete from agent_workers where worker_id=%s",
                (row["worker_id"],),
            )
            deleted.append(str(row["worker_id"]))
            continue
        scope = sorted(
            {
                str(token_row["workspace_id"])
                for token_row in conn.execute(
                    "select workspace_id from agent_register_tokens where id = any(%s)",
                    (live,),
                ).fetchall()
            }
        )
        conn.execute(
            "update agent_workers"
            " set register_token_ids_json=%s, allowed_workspaces_json=%s"
            " where worker_id=%s",
            (json.dumps(sorted(live)), json.dumps(scope), row["worker_id"]),
        )
    return deleted
