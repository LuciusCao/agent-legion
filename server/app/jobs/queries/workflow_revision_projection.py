"""Agent-route projection writes for published workflow revisions (#287).

Why a separate module: ``workspace_node_routes`` /
``workspace_node_capacities`` are *materialized projections* of the active
revision, not revision rows themselves — their write path (upsert + stale-row
prune) is a distinct concern from ``workflow_revisions`` row persistence.
Publication and the startup reconcile both funnel through these helpers, so
the projection SQL lives in one place instead of being inlined twice by the
revision write path.

Every helper takes an open connection and never commits on its own: the
projection must land atomically with the revision insert that publishes it
(single transaction in ``WorkflowRevisionQueriesMixin.create_workflow_revision``).
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection


def _upsert_agent_route(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    agent_id: str,
) -> None:
    conn.execute(
        """
        insert into workspace_node_routes(
          workspace_id, node_key, target_kind, target_id
        ) values (%s, %s, 'agent', %s)
        on conflict(workspace_id, node_key) do update set
          target_kind='agent', target_id=excluded.target_id
        """,
        (workspace_id, node_key, agent_id),
    )


def _delete_stale_projection_rows(
    conn: DatabaseConnection,
    workspace_id: str,
    workflow_key: str,
    keep_route_nodes: set[str],
    keep_capacity_nodes: set[str],
) -> None:
    """Remove route/capacity rows absent from the new projection.

    ``workspace_node_routes``/``workspace_node_capacities`` are materialized
    projections of the current published revision, so rows for nodes that lost
    their Agent routing (or were removed) must not survive a republish. Only
    ``target_kind='agent'`` routes are pruned; handler routes are owned by a
    different write path.
    """
    # #211 Phase 3 (read-layer binding): prune predicates key on workspace_id
    # alone — workflow_key equals it on every row (v62 binding, aligned by
    # v68), so the column filter was redundant.
    if keep_route_nodes:
        placeholders = ", ".join("%s" for _ in keep_route_nodes)
        conn.execute(
            "delete from workspace_node_routes"
            " where workspace_id=%s and target_kind='agent'"
            f" and node_key not in ({placeholders})",
            (workspace_id, *sorted(keep_route_nodes)),
        )
    else:
        conn.execute(
            "delete from workspace_node_routes where workspace_id=%s and target_kind='agent'",
            (workspace_id,),
        )
    if keep_capacity_nodes:
        placeholders = ", ".join("%s" for _ in keep_capacity_nodes)
        conn.execute(
            "delete from workspace_node_capacities"
            " where workspace_id=%s"
            f" and node_key not in ({placeholders})",
            (workspace_id, *sorted(keep_capacity_nodes)),
        )
    else:
        conn.execute(
            "delete from workspace_node_capacities where workspace_id=%s",
            (workspace_id,),
        )


def write_agent_route_projection(
    conn: DatabaseConnection,
    *,
    workspace_id: str,
    workflow_key: str,
    agent_routes: dict[str, str],
) -> None:
    """Replace the workspace's agent-route projection (upsert + prune).

    Runs inside the caller's transaction. No ``workspace_node_capacities``
    writes: Agent capacity is workspace-level now; the prune clears legacy
    per-node rows.
    """
    for node_key, agent_id in agent_routes.items():
        _upsert_agent_route(conn, workspace_id, workflow_key, node_key, agent_id)
    _delete_stale_projection_rows(
        conn,
        workspace_id,
        workflow_key,
        keep_route_nodes=set(agent_routes),
        keep_capacity_nodes=set(),
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
    agent_routes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Insert one revision row and, for publishes, rewrite the projection.

    Archive-then-insert keeps a workspace's active revision singular; the
    projection rewrite rides the same transaction so a revision is never
    visible with another revision's routes. Returns the inserted row (or
    None when the row vanished between insert and re-read, which the caller
    turns into an error).
    """
    if status == "active":
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
    if agent_routes is not None:
        write_agent_route_projection(
            conn,
            workspace_id=workspace_id,
            workflow_key=workflow_key,
            agent_routes=agent_routes,
        )
    return conn.execute("select * from workflow_revisions where id=%s", (revision_id,)).fetchone()
