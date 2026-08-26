"""Data migration applied alongside the idempotent DDL replay (v25)."""

from __future__ import annotations

from typing import Any

_CODE_EXECUTOR_ID = "code-default"
_LOCAL_EXECUTOR_ID = "local-default"


def migrate_local_executor_removal(conn: Any) -> None:
    """Retire the local executor kind (v25).

    The remaining seven ``local-default`` capabilities moved to
    ``code-default`` (config/workflow.yaml), so every workspace binding that
    still points at ``local-default`` is repointed to ``code-default`` and
    the ``local-default`` allocation is deleted. Node limits
    (``workspace_node_limits``) are keyed by workflow/node, not executor, and
    now apply to the code kind, so they are kept as-is. Idempotent: the
    update and delete converge on replay.
    """
    conn.execute(
        "update workspace_node_bindings set executor_id=%s where executor_id=%s",
        (_CODE_EXECUTOR_ID, _LOCAL_EXECUTOR_ID),
    )
    conn.execute(
        "delete from workspace_executor_allocations where executor_id=%s",
        (_LOCAL_EXECUTOR_ID,),
    )
