"""In-transaction revalidation of registration keys (SECURITY-WORKER-001).

The HTTP register route resolves the presented keys in a read-only
transaction; the worker row is written by a separate one. Between the two,
an admin deleting a key would not see the not-yet-written worker row, so the
registration would go through and mint a worker_token bound to a deleted
key. issue_token therefore re-checks every bound key inside its write
transaction, locking the register-token rows so the cascade and the
registration serialize on the key instead of racing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class RegisterKeyDeleted(KeyError):
    """A registration-admission key no longer exists at write time."""


def resolve_issue_scope(
    conn: Any, register_token_ids: Sequence[str], allowed_workspaces: Sequence[str] | None
) -> list[str]:
    """Derive the stored workspace scope inside the issue_token transaction.

    Key-bound registrations (the HTTP contract) revalidate and lock their
    register-token rows and take the scope from the surviving rows; raises
    RegisterKeyDeleted when any bound key is gone. Binding-less calls
    (legacy direct registry callers) keep validating the explicit
    allowed_workspaces list against the workspaces table."""
    if register_token_ids:
        return locked_register_scope(conn, register_token_ids)
    scope = sorted({str(workspace) for workspace in (allowed_workspaces or [])})
    for workspace in scope:
        exists = conn.execute("select 1 from workspaces where id=%s", (workspace,)).fetchone()
        if exists is None:
            raise ValueError(f"workspace {workspace!r} does not exist")
    return scope


def locked_register_scope(conn: Any, register_token_ids: Sequence[str]) -> list[str]:
    """Revalidate the bound register-token rows under lock, in `conn`'s
    transaction, and return the surviving keys' workspace ids.

    Raises RegisterKeyDeleted when any bound key no longer exists (deleted or
    never issued): the caller must abort the registration, not persist a
    worker whose admission keys are already dead."""
    rows = conn.execute(
        "select id, workspace_id from agent_register_tokens"
        " where id = any(%s) order by id for update",
        (register_token_ids,),
    ).fetchall()
    surviving = {str(row["id"]) for row in rows}
    missing = [token_id for token_id in register_token_ids if token_id not in surviving]
    if missing:
        raise RegisterKeyDeleted(
            "register token no longer exists: "
            + ", ".join(sorted(missing))
            + " — re-register with current keys"
        )
    return sorted({str(row["workspace_id"]) for row in rows})
