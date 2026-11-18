"""Historical projection pruning for published workflow revisions (#287, #936).

``workspace_node_routes`` rows are frozen historical targets, not rewritten
from the new revision. Publication only prunes rows of deleted/retyped nodes
and clears the obsolete per-node Agent capacity projection. Every helper takes
an open connection and never commits on its own: pruning lands atomically with
the revision insert in ``WorkflowRevisionQueriesMixin.create_workflow_revision``.
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.upgrade_impl_identity import (
    acquire_implementation_publication_lock,
)


def prune_frozen_agent_routes(
    conn: DatabaseConnection, workspace_id: str, keep_nodes: frozenset[str]
) -> None:
    """Prune stale historical routes and obsolete per-node capacity rows.

    Every revision publish keeps the existing route target of nodes still
    declared ``type: agent``; only rows of removed or retyped nodes are
    deleted. Handler routes belong to a different write path. Agent capacity
    is workspace-level now, so its legacy per-node projection is still cleared.
    """
    # #211 Phase 3 (read-layer binding): prune predicates key on workspace_id
    # alone — workflow_key equals it on every row (v62 binding, aligned by
    # v68), so the column filter was redundant.
    if keep_nodes:
        placeholders = ", ".join("%s" for _ in keep_nodes)
        conn.execute(
            "delete from workspace_node_routes where workspace_id=%s and target_kind='agent'"
            f" and node_key not in ({placeholders})",
            (workspace_id, *sorted(keep_nodes)),
        )
    else:
        conn.execute(
            "delete from workspace_node_routes where workspace_id=%s and target_kind='agent'",
            (workspace_id,),
        )
    conn.execute(
        "delete from workspace_node_capacities where workspace_id=%s",
        (workspace_id,),
    )


def create_workflow_revision_with_projection(
    conn: DatabaseConnection,
    *,
    revision_id: str,
    workspace_id: str,
    workflow_key: str,
    version: int,
    status: str,
    definition_json: str,
    definition_hash: str,
    frozen_route_nodes: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """Insert one revision row and atomically prune stale historical projections.

    Archive-then-insert keeps a workspace's active revision singular. Existing
    Agent route targets are never inserted or updated; only their retype/delete
    prune rides the revision transaction. Returns the inserted row (or None
    when the row vanished between insert and re-read, which the caller turns
    into an error).
    """
    if status == "active":
        # #759 P2-A：发布进入 implementation-publication 锁域（与
        # versioned_entities 的 Agent/node_code publish/rollback/archive、
        # upgrade guard 的重读/重验三成员同域，锁序见
        # job_workflow_upgrade_apply）——guard 重读到其提交之间的发布被
        # 挡住，upgrade 不会 pin 到刚被取代的旧 revision。发布不碰 job
        # 行、不取 job-mutation 锁，无反向依赖（锁序无环）。
        acquire_implementation_publication_lock(conn, workspace_id)
        # #211 Phase 3 (read-layer binding): the archive predicate keys on
        # workspace_id — one active revision per workspace (v62 binding).
        conn.execute(
            """
            update workflow_revisions
            set status='archived'
            where workspace_id=%s and status='active'
            """,
            (workspace_id,),
        )
    conn.execute(
        """
        insert into workflow_revisions(
          id, workspace_id, version, status, definition_json, definition_hash, published_at
        )
        values (%s, %s, %s, %s, %s, %s, case when %s='active' then current_timestamp else null end)
        """,
        (
            revision_id,
            workspace_id,
            version,
            status,
            definition_json,
            definition_hash,
            status,
        ),
    )
    if frozen_route_nodes is not None:
        prune_frozen_agent_routes(conn, workspace_id, frozen_route_nodes)
    return conn.execute("select * from workflow_revisions where id=%s", (revision_id,)).fetchone()
