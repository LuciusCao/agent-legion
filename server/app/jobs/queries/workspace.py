from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from server.app.executors._lease_lifecycle import active_lease_counts
from server.app.jobs.executor_configuration import (
    get_workspace_executor_configuration,
    replace_workspace_executor_configuration,
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


def _workspace_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["cms_config"] = _decode_json_object(record.get("cms_config_json"))
    record["resource_config"] = _decode_json_object(record.get("resource_config_json"))
    record["intake_config"] = _decode_json_object(record.get("intake_config_json"))
    return record


class WorkspaceQueriesMixin(JobQueriesBase):
    jobs_dir: Path

    def create_workspace(
        self,
        name: str,
        default_workflow_key: str = "question_content",
        cms_config: dict[str, Any] | None = None,
        resource_config: dict[str, Any] | None = None,
        default_entity: str = "question",
        intake_config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        cms_config_json = json.dumps(cms_config or {}, ensure_ascii=False, sort_keys=True)
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
            while conn.execute("select 1 from workspaces where id=?", (workspace_id,)).fetchone():
                workspace_id = f"{base_id}_{suffix}"
                suffix += 1

            conn.execute(
                """
                insert into workspaces(
                  id, name, description, default_workflow_key, cms_config_json, resource_config_json,
                  default_entity, intake_config_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    clean_name,
                    clean_description,
                    default_workflow_key,
                    cms_config_json,
                    resource_config_json,
                    clean_entity,
                    intake_config_json,
                ),
            )
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row)

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from workspaces order by created_at, id")
            return [_workspace_record(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row) if row else None

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_workflow_key: str | None = None,
        cms_config: dict[str, Any] | None = None,
        resource_config: dict[str, Any] | None = None,
        default_entity: str | None = None,
        intake_config: dict[str, Any] | None = None,
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
        if cms_config is not None:
            fields["cms_config_json"] = json.dumps(
                cms_config,
                ensure_ascii=False,
                sort_keys=True,
            )
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
        if not fields:
            workspace = self.get_workspace(workspace_id)
            if workspace is None:
                raise ValueError("Workspace not found")
            return workspace

        assignments = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [workspace_id]
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                update workspaces
                set {assignments}, updated_at=current_timestamp
                where id=?
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
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
        executor_allocations: Sequence[Mapping[str, Any]] | None = None,
        node_bindings: Sequence[Mapping[str, Any]] | None = None,
        node_limits: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        with self.connect() as conn:
            exists = conn.execute("select 1 from workspaces where id=?", (workspace_id,)).fetchone()
            if exists is None:
                raise ValueError("Workspace not found")
            conn.execute(
                """
                update workspaces
                set name=?, description=?, default_workflow_key=?, default_entity=?,
                    resource_config_json=?, intake_config_json=?,
                    updated_at=current_timestamp
                where id=?
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
            replace_workspace_executor_configuration(
                conn,
                workspace_id,
                executor_allocations or [],
                node_bindings or [],
                node_limits or [],
            )
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row)

    def delete_workspace(self, workspace_id: str) -> None:
        with self.connect() as conn:
            running = conn.execute(
                "select 1 from jobs where workspace_id = ? and status = ?",
                (workspace_id, "running"),
            ).fetchone()
            if running is not None:
                raise ValueError("Cannot delete workspace with running jobs")
            conn.execute(
                "delete from job_nodes where job_id in (select id from jobs where workspace_id = ?)",
                (workspace_id,),
            )
            conn.execute(
                "delete from node_runs where job_id in (select id from jobs where workspace_id = ?)",
                (workspace_id,),
            )
            conn.execute(
                "delete from job_batches where workspace_id = ?",
                (workspace_id,),
            )
            conn.execute(
                "delete from jobs where workspace_id = ?",
                (workspace_id,),
            )
            cursor = conn.execute(
                "delete from workspaces where id = ?",
                (workspace_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")

    def get_workspace_executor_configuration(
        self, workspace_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            return get_workspace_executor_configuration(conn, workspace_id)

    def get_workspace_executor_runtime_counts(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return per-executor allocation, binding, and active-lease counts.

        The result is a list of dicts with ``executor_id``, ``workspace_limit``,
        ``running`` (active leases for this workspace), ``global_running`` (active
        leases across all workspaces), and ``binding_count`` (bindings in this
        workspace for this executor). Global capacities and executor kinds are
        intentionally left to the caller so the repository stays decoupled from
        runtime executor definitions.
        """
        with self._connect_read() as conn:
            config = get_workspace_executor_configuration(conn, workspace_id)
            binding_counts: dict[str, int] = {}
            for binding in config["bindings"]:
                binding_counts[binding["executor_id"]] = (
                    binding_counts.get(binding["executor_id"], 0) + 1
                )

            result: list[dict[str, Any]] = []
            for allocation in config["allocations"]:
                executor_id = allocation["executor_id"]
                counts = active_lease_counts(conn, executor_id)
                result.append(
                    {
                        "executor_id": executor_id,
                        "workspace_limit": allocation["concurrency_limit"],
                        "running": counts.get(workspace_id, 0),
                        "global_running": counts.get("global", 0),
                        "binding_count": binding_counts.get(executor_id, 0),
                    }
                )
            return result

    def replace_workspace_executor_configuration(
        self, workspace_id, allocations, bindings, node_limits
    ):
        with self.connect() as conn:
            replace_workspace_executor_configuration(
                conn, workspace_id, allocations, bindings, node_limits
            )
