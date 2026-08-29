"""Data migration applied alongside the idempotent DDL replay (v24)."""

from __future__ import annotations

from typing import Any

_CODE_EXECUTOR_ID = "code-default"
_CODE_NODE_BINDINGS = (
    ("question_comprehension_info", "fetch_questions"),
    ("video_knowledge", "download_video"),
)


def migrate_code_executor_bindings(conn: Any) -> None:
    """Rebind the two CMS first nodes to the code executor (v24).

    Existing workspaces bound ``fetch_questions`` / ``download_video`` to
    ``local-default``; the code executor now owns those capabilities
    (config/workflow.yaml), so each workspace gets a ``code-default``
    allocation (concurrency copied from its ``local-default`` allocation,
    default 1) and the two node bindings are repointed. Idempotent: the
    allocation insert is ``on conflict do nothing`` and the binding update
    converges to the same value on replay.
    """
    workspaces = conn.execute("select id from workspaces").fetchall()
    for row in workspaces:
        workspace_id = row["id"]
        local = conn.execute(
            "select concurrency_limit from workspace_executor_allocations"
            " where workspace_id=%s and executor_id='local-default'",
            (workspace_id,),
        ).fetchone()
        concurrency = int(local["concurrency_limit"]) if local else 1
        conn.execute(
            "insert into workspace_executor_allocations"
            "(workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)"
            " on conflict(workspace_id, executor_id) do nothing",
            (workspace_id, _CODE_EXECUTOR_ID, concurrency),
        )
        for workflow_key, node_key in _CODE_NODE_BINDINGS:
            conn.execute(
                "update workspace_node_bindings set executor_id=%s"
                " where workspace_id=%s and workflow_key=%s and node_key=%s",
                (_CODE_EXECUTOR_ID, workspace_id, workflow_key, node_key),
            )
