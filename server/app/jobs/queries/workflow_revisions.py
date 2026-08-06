from __future__ import annotations

from typing import Any

from server.app.jobs.queries.base import JobQueriesBase


def _delete_stale_projection_rows(
    conn,
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
    if keep_route_nodes:
        placeholders = ", ".join("%s" for _ in keep_route_nodes)
        conn.execute(
            "delete from workspace_node_routes"
            " where workspace_id=%s and workflow_key=%s and target_kind='agent'"
            f" and node_key not in ({placeholders})",
            (workspace_id, workflow_key, *sorted(keep_route_nodes)),
        )
    else:
        conn.execute(
            "delete from workspace_node_routes"
            " where workspace_id=%s and workflow_key=%s and target_kind='agent'",
            (workspace_id, workflow_key),
        )
    if keep_capacity_nodes:
        placeholders = ", ".join("%s" for _ in keep_capacity_nodes)
        conn.execute(
            "delete from workspace_node_capacities"
            " where workspace_id=%s and workflow_key=%s"
            f" and node_key not in ({placeholders})",
            (workspace_id, workflow_key, *sorted(keep_capacity_nodes)),
        )
    else:
        conn.execute(
            "delete from workspace_node_capacities where workspace_id=%s and workflow_key=%s",
            (workspace_id, workflow_key),
        )


class WorkflowRevisionQueriesMixin(JobQueriesBase):
    def materialize_agent_routes(
        self,
        *,
        workspace_id: str,
        workflow_key: str,
        agent_routes: dict[str, str],
    ) -> None:
        with self.connect() as conn:
            for node_key, agent_id in agent_routes.items():
                conn.execute(
                    """
                    insert into workspace_node_routes(
                      workspace_id, workflow_key, node_key, target_kind, target_id
                    ) values (%s, %s, %s, 'agent', %s)
                    on conflict(workspace_id, workflow_key, node_key) do update set
                      target_kind='agent', target_id=excluded.target_id
                    """,
                    (workspace_id, workflow_key, node_key, agent_id),
                )
            # No workspace_node_capacities writes: Agent capacity is
            # workspace-level now. The prune clears legacy per-node rows.
            _delete_stale_projection_rows(
                conn,
                workspace_id,
                workflow_key,
                keep_route_nodes=set(agent_routes),
                keep_capacity_nodes=set(),
            )

    def create_workflow_revision(
        self,
        *,
        revision_id: str,
        workspace_id: str,
        workflow_key: str,
        version: int,
        status: str,
        definition_json: str,
        definition_hash: str,
        agent_routes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            if status == "active":
                conn.execute(
                    """
                    update workflow_revisions
                    set status='archived'
                    where workspace_id=%s and workflow_key=%s and status='active'
                    """,
                    (workspace_id, workflow_key),
                )
            conn.execute(
                """
                insert into workflow_revisions(
                  id, workspace_id, workflow_key, version, status, definition_json, definition_hash, published_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, case when %s='active' then current_timestamp else null end)
                """,
                (
                    revision_id,
                    workspace_id,
                    workflow_key,
                    version,
                    status,
                    definition_json,
                    definition_hash,
                    status,
                ),
            )
            for node_key, agent_id in (agent_routes or {}).items():
                conn.execute(
                    """
                    insert into workspace_node_routes(
                      workspace_id, workflow_key, node_key, target_kind, target_id
                    ) values (%s, %s, %s, 'agent', %s)
                    on conflict(workspace_id, workflow_key, node_key) do update set
                      target_kind='agent', target_id=excluded.target_id
                    """,
                    (workspace_id, workflow_key, node_key, agent_id),
                )
            if agent_routes is not None:
                # No workspace_node_capacities writes (workspace-level Agent
                # capacity); the prune clears legacy per-node rows.
                _delete_stale_projection_rows(
                    conn,
                    workspace_id,
                    workflow_key,
                    keep_route_nodes=set(agent_routes or {}),
                    keep_capacity_nodes=set(),
                )
            row = conn.execute(
                "select * from workflow_revisions where id=%s", (revision_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("workflow revision insert did not return a row")
        return dict(row)

    def get_active_workflow_revision(
        self, workspace_id: str, workflow_key: str
    ) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=%s and workflow_key=%s and status='active'
                order by version desc
                limit 1
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return dict(row) if row else None

    def list_active_workflow_revisions(self) -> list[dict[str, Any]]:
        """All active revisions across workspaces (at most one per workspace/workflow)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where status='active'
                order by workspace_id, workflow_key
                """,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_workflow_revision(
        self,
        workspace_id: str,
        workflow_key: str,
        revision_id: str,
    ) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select * from workflow_revisions
                where id=%s and workspace_id=%s and workflow_key=%s
                limit 1
                """,
                (revision_id, workspace_id, workflow_key),
            ).fetchone()
        return dict(row) if row else None

    def next_workflow_revision_version(self, workspace_id: str, workflow_key: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select coalesce(max(version), 0) + 1 as next_version
                from workflow_revisions
                where workspace_id=%s and workflow_key=%s
                """,
                (workspace_id, workflow_key),
            ).fetchone()
        return int(row["next_version"]) if row is not None else 1

    def list_workflow_revisions(self, workspace_id: str, workflow_key: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select * from workflow_revisions
                where workspace_id=%s and workflow_key=%s
                order by version desc
                """,
                (workspace_id, workflow_key),
            ).fetchall()
        return [dict(row) for row in rows]
