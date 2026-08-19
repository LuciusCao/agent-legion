from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from server.app.jobs.node_limits import (
    get_workspace_node_limits,
    replace_workspace_node_limits,
)
from server.app.jobs.queries.base import JobQueriesBase


def _safe_identifier(value: str, fallback: str) -> str:
    safe_value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_")
    return safe_value or fallback


def _workspace_id(name: str) -> str:
    return _safe_identifier(name.lower(), "workspace")


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _workspace_record(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["resource_config"] = _decode_json_object(record.get("resource_config_json"))
    record["intake_config"] = _decode_json_object(record.get("intake_config_json"))
    record["node_config"] = _decode_json_object(record.get("node_config_json"))
    return record


class WorkspaceQueriesMixin(JobQueriesBase):
    jobs_dir: Path

    def create_workspace(
        self,
        name: str,
        default_workflow_key: str,
        resource_config: dict[str, Any] | None = None,
        default_entity: str = "question",
        intake_config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        # The workflow key slot may stay empty (schema v50): a blank-canvas
        # workspace has no workflow until the first publish adopts one.
        clean_workflow_key = (default_workflow_key or "").strip()
        resource_config_json = json.dumps(
            resource_config or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        clean_entity = (default_entity or "question").strip() or "question"
        intake_config_json = json.dumps(intake_config or {}, ensure_ascii=False, sort_keys=True)
        clean_description = (description or "").strip()

        base_id = _workspace_id(clean_name)
        with self.connect() as conn:
            workspace_id = base_id
            suffix = 2
            while conn.execute("select 1 from workspaces where id=%s", (workspace_id,)).fetchone():
                workspace_id = f"{base_id}_{suffix}"
                suffix += 1

            conn.execute(
                """
                insert into workspaces(
                  id, name, description, default_workflow_key, resource_config_json,
                  default_entity, intake_config_json
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    workspace_id,
                    clean_name,
                    clean_description,
                    clean_workflow_key,
                    resource_config_json,
                    clean_entity,
                    intake_config_json,
                ),
            )
            row = conn.execute("select * from workspaces where id=%s", (workspace_id,)).fetchone()
        if row is None:
            raise RuntimeError("workspace insert did not return a row")
        return _workspace_record(row)

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from workspaces order by created_at, id")
            return [_workspace_record(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from workspaces where id=%s", (workspace_id,)).fetchone()
        return _workspace_record(row) if row else None

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_workflow_key: str | None = None,
        resource_config: dict[str, Any] | None = None,
        default_entity: str | None = None,
        intake_config: dict[str, Any] | None = None,
        node_config: dict[str, Any] | None = None,
        default_agent_provider: str | None = None,
        default_agent_model: str | None = None,
        default_agent_thinking: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Workspace name is required")
            fields["name"] = clean_name
        if description is not None:
            fields["description"] = description.strip()
        if default_workflow_key is not None:
            fields["default_workflow_key"] = default_workflow_key
        if resource_config is not None:
            fields["resource_config_json"] = json.dumps(
                resource_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if default_entity is not None:
            clean_entity = (default_entity or "question").strip() or "question"
            fields["default_entity"] = clean_entity
        if intake_config is not None:
            fields["intake_config_json"] = json.dumps(
                intake_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if node_config is not None:
            fields["node_config_json"] = json.dumps(
                node_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if default_agent_provider is not None:
            fields["default_agent_provider"] = default_agent_provider.strip()
        if default_agent_model is not None:
            fields["default_agent_model"] = default_agent_model.strip()
        if default_agent_thinking is not None:
            fields["default_agent_thinking"] = default_agent_thinking.strip()
        if not fields:
            workspace = self.get_workspace(workspace_id)
            if workspace is None:
                raise ValueError("Workspace not found")
            return workspace

        assignments = ", ".join(f"{key}=%s" for key in fields)
        params = list(fields.values()) + [workspace_id]
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                update workspaces
                set {assignments}, updated_at=current_timestamp
                where id=%s
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")
            row = conn.execute("select * from workspaces where id=%s", (workspace_id,)).fetchone()
        if row is None:
            raise RuntimeError("workspace update did not return a row")
        return _workspace_record(row)

    def update_workspace_configuration(
        self,
        workspace_id: str,
        *,
        name: str,
        description: str,
        default_workflow_key: str,
        default_entity: str,
        resource_config: dict[str, Any],
        intake_config: dict[str, Any],
        node_limits: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        with self.connect() as conn:
            exists = conn.execute(
                "select 1 from workspaces where id=%s", (workspace_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("Workspace not found")
            conn.execute(
                """
                update workspaces
                set name=%s, description=%s, default_workflow_key=%s, default_entity=%s,
                    resource_config_json=%s, intake_config_json=%s,
                    updated_at=current_timestamp
                where id=%s
                """,
                (
                    clean_name,
                    description.strip(),
                    default_workflow_key,
                    default_entity,
                    json.dumps(resource_config, ensure_ascii=False, sort_keys=True),
                    json.dumps(intake_config, ensure_ascii=False, sort_keys=True),
                    workspace_id,
                ),
            )
            replace_workspace_node_limits(
                conn,
                workspace_id,
                node_limits or [],
            )
            row = conn.execute("select * from workspaces where id=%s", (workspace_id,)).fetchone()
        if row is None:
            raise RuntimeError("workspace configuration update did not return a row")
        return _workspace_record(row)

    def delete_workspace(self, workspace_id: str) -> None:
        with self.connect() as conn:
            running = conn.execute(
                "select 1 from jobs where workspace_id = %s and status = %s",
                (workspace_id, "running"),
            ).fetchone()
            if running is not None:
                raise ValueError("Cannot delete workspace with running jobs")
            conn.execute(
                "delete from job_nodes where job_id in (select id from jobs where workspace_id = %s)",
                (workspace_id,),
            )
            conn.execute(
                "delete from node_runs where job_id in (select id from jobs where workspace_id = %s)",
                (workspace_id,),
            )
            conn.execute(
                "delete from job_batches where workspace_id = %s",
                (workspace_id,),
            )
            conn.execute(
                "delete from jobs where workspace_id = %s",
                (workspace_id,),
            )
            cursor = conn.execute(
                "delete from workspaces where id = %s",
                (workspace_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")

    def get_workspace_node_limits(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return get_workspace_node_limits(conn, workspace_id)

    def get_workspace_agent_capacity(self, workspace_id: str) -> int | None:
        """Current workspace-level Agent concurrency limit; None when unset."""
        with self._connect_read() as conn:
            row = conn.execute(
                "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
                (workspace_id,),
            ).fetchone()
        return int(row["max_concurrency"]) if row is not None else None

    def set_workspace_agent_capacity(self, workspace_id: str, max_concurrency: int) -> None:
        if max_concurrency <= 0:
            raise ValueError("Agent capacity must be a positive integer")
        with self.connect() as conn:
            conn.execute(
                """
                insert into workspace_agent_capacities(workspace_id, max_concurrency, updated_at)
                values (%s, %s, current_timestamp)
                on conflict(workspace_id) do update set
                  max_concurrency=excluded.max_concurrency,
                  updated_at=current_timestamp
                """,
                (workspace_id, max_concurrency),
            )

    def get_code_pool_counts(self, workspace_id: str) -> dict[str, int]:
        """Active code-pool lease counts: this workspace's and the global total.

        Only local code-pool leases (executor_id 'code') count; Worker-claimed
        executions are capacity-accounted on the Worker side (P-0.5).
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select workspace_id, count(*) as cnt
                from executor_leases
                where executor_id='code' and status='active' and expires_at>current_timestamp
                group by workspace_id
                """
            ).fetchall()
        counts = {"running": 0, "global_running": 0}
        for row in rows:
            if str(row["workspace_id"]) == workspace_id:
                counts["running"] = int(row["cnt"])
            counts["global_running"] += int(row["cnt"])
        return counts
