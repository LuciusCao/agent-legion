from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from server.app.db.migrations.report import MigrationIssue, MigrationReport, raise_blocked
from server.app.db.migrations.v005_remove_legacy_executor_paths import (
    MIGRATION as V005_MIGRATION,
)
from server.app.executors.backup import backup_sqlite_connection
from server.app.executors.config import ExecutorConfig
from server.app.executors.legacy_configuration import (
    ExistingConfiguration,
    collect_existing_configuration,
)
from server.app.jobs import executor_configuration
from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)

_DEFAULT_LOCAL_EXECUTOR_ID = "local-default"
_DEFAULT_PI_EXECUTOR_ID = "pi"
_MIGRATION_VERSION = 5
_MIGRATION_NAME = "remove_legacy_executor_paths"


def _decode_json_object(value: Any) -> tuple[dict[str, Any], str | None]:
    if not value:
        return {}, None
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON at character {exc.pos}"
    if not isinstance(loaded, dict):
        return {}, f"expected a JSON object, got {type(loaded).__name__}"
    return loaded, None


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _executor_supports(
    executor_id: str, capability: str, executors: dict[str, ExecutorConfig]
) -> bool:
    config = executors.get(executor_id)
    if config is None:
        return False
    return capability in config.capabilities


def _version_is_recorded(conn: sqlite3.Connection, version: int) -> bool:
    try:
        row = conn.execute(
            "select 1 from schema_migrations where version = ?",
            (version,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _collect_legacy_data(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Load all Workspaces, assignments, pipeline config, and bootstrap state."""
    workspaces: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "select id, default_workflow_key, pipeline_config_json from workspaces"
        ).fetchall()
        for row in rows:
            pipeline_config, pipeline_config_error = _decode_json_object(
                row["pipeline_config_json"]
            )
            workspaces[row["id"]] = {
                "default_workflow_key": row["default_workflow_key"],
                "pipeline_config": pipeline_config,
                "pipeline_config_error": pipeline_config_error,
                "agent_assignments": [],
                "authoritative": False,
            }
    except sqlite3.OperationalError:
        # workspaces table may not exist in an uninitialised read-only check.
        return {}

    if _table_exists(conn, "workspace_agent_assignments"):
        rows = conn.execute(
            "select workspace_id, agent_id, concurrency_limit from workspace_agent_assignments"
        ).fetchall()
        for row in rows:
            ws = workspaces.get(row["workspace_id"])
            if ws is not None:
                ws["agent_assignments"].append(
                    {"agent_id": row["agent_id"], "concurrency_limit": row["concurrency_limit"]}
                )

    if _table_exists(conn, "workspace_executor_bootstrap_state"):
        rows = conn.execute(
            "select workspace_id from workspace_executor_bootstrap_state"
        ).fetchall()
        for row in rows:
            ws = workspaces.get(row["workspace_id"])
            if ws is not None:
                ws["authoritative"] = True

    return workspaces


def _preflight_workspace(
    workspace_id: str,
    workspace: dict[str, Any],
    definitions_by_key: dict[str, WorkflowDefinition],
    executors: dict[str, ExecutorConfig],
) -> list[MigrationIssue]:
    issues: list[MigrationIssue] = []

    if workspace["authoritative"]:
        return issues

    pipeline_config_error = workspace.get("pipeline_config_error")
    if pipeline_config_error:
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="pipeline_config_json",
                message=str(pipeline_config_error),
            )
        )
        return issues

    workflow_key = workspace["default_workflow_key"]
    definition = definitions_by_key.get(workflow_key)
    if definition is None:
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="default_workflow_key",
                message=f"workflow {workflow_key!r} has no registered definition",
            )
        )
        return issues

    pipeline_config = workspace["pipeline_config"]
    local_limit_raw = pipeline_config.get("local")
    agent_limit_raw = pipeline_config.get("agent")

    if local_limit_raw is not None and not _is_positive_int(local_limit_raw):
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="pipeline_config_json.local",
                message=f"local concurrency limit must be a positive integer, got {local_limit_raw!r}",
            )
        )

    if agent_limit_raw is not None and not _is_positive_int(agent_limit_raw):
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="pipeline_config_json.agent",
                message=f"agent concurrency limit must be a positive integer, got {agent_limit_raw!r}",
            )
        )

    node_limits_raw = pipeline_config.get("nodes", {})
    if not isinstance(node_limits_raw, dict):
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="pipeline_config_json.nodes",
                message="node limits must be a mapping",
            )
        )
        node_limits_raw = {}

    for node_key, limit in node_limits_raw.items():
        if not _is_positive_int(limit):
            issues.append(
                MigrationIssue(
                    table="workspaces",
                    row_key=workspace_id,
                    constraint=f"pipeline_config_json.nodes.{node_key}",
                    message=f"node concurrency limit must be a positive integer, got {limit!r}",
                )
            )

    pi_assignment: dict[str, Any] | None = None
    for assignment in workspace["agent_assignments"]:
        agent_id = assignment["agent_id"]
        limit = assignment["concurrency_limit"]
        if agent_id != "pi":
            issues.append(
                MigrationIssue(
                    table="workspace_agent_assignments",
                    row_key=f"{workspace_id}/{agent_id}",
                    constraint="agent_id",
                    message=f"unknown legacy agent {agent_id!r}; manual remediation required",
                )
            )
        elif not _is_positive_int(limit):
            issues.append(
                MigrationIssue(
                    table="workspace_agent_assignments",
                    row_key=f"{workspace_id}/{agent_id}",
                    constraint="concurrency_limit",
                    message=f"concurrency limit must be a positive integer, got {limit!r}",
                )
            )
        else:
            pi_assignment = assignment

    needs_local = any(
        _executor_supports(_DEFAULT_LOCAL_EXECUTOR_ID, node.capability, executors)
        for node in definition.nodes.values()
    )
    needs_pi = pi_assignment is not None

    if needs_local and _DEFAULT_LOCAL_EXECUTOR_ID not in executors:
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="executor_id",
                message=f"required executor {_DEFAULT_LOCAL_EXECUTOR_ID!r} is not configured",
            )
        )

    if needs_pi and _DEFAULT_PI_EXECUTOR_ID not in executors:
        issues.append(
            MigrationIssue(
                table="workspaces",
                row_key=workspace_id,
                constraint="executor_id",
                message=f"required executor {_DEFAULT_PI_EXECUTOR_ID!r} is not configured",
            )
        )

    return issues


def _materialize_workspace(
    conn: sqlite3.Connection,
    workspace_id: str,
    workspace: dict[str, Any],
    definition: WorkflowDefinition,
    executors: dict[str, ExecutorConfig],
    existing: ExistingConfiguration,
) -> None:
    if workspace["authoritative"]:
        return

    pipeline_config = workspace["pipeline_config"]
    local_limit = pipeline_config.get("local")
    if not _is_positive_int(local_limit):
        local_limit = 1

    pi_assignment = next(
        (a for a in workspace["agent_assignments"] if a["agent_id"] == "pi"),
        None,
    )
    pi_limit = pi_assignment["concurrency_limit"] if pi_assignment else None

    node_limits_raw = pipeline_config.get("nodes", {})
    if not isinstance(node_limits_raw, dict):
        node_limits_raw = {}

    needs_local = any(
        _executor_supports(_DEFAULT_LOCAL_EXECUTOR_ID, node.capability, executors)
        for node in definition.nodes.values()
    )
    needs_pi = pi_assignment is not None

    if needs_local and _DEFAULT_LOCAL_EXECUTOR_ID not in existing.allocations:
        assert _is_positive_int(local_limit)
        conn.execute(
            """
            insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            """,
            (workspace_id, _DEFAULT_LOCAL_EXECUTOR_ID, int(local_limit)),
        )

    if needs_pi and _DEFAULT_PI_EXECUTOR_ID not in existing.allocations:
        assert pi_limit is not None and _is_positive_int(pi_limit)
        conn.execute(
            """
            insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            """,
            (workspace_id, _DEFAULT_PI_EXECUTOR_ID, int(pi_limit)),
        )

    for node in definition.nodes.values():
        binding_key = (definition.key, node.key)
        local_supported = _executor_supports(_DEFAULT_LOCAL_EXECUTOR_ID, node.capability, executors)
        pi_supported = _executor_supports(_DEFAULT_PI_EXECUTOR_ID, node.capability, executors)

        if local_supported:
            if binding_key not in existing.bindings:
                conn.execute(
                    """
                    insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id)
                    values (?, ?, ?, ?)
                    """,
                    (workspace_id, definition.key, node.key, _DEFAULT_LOCAL_EXECUTOR_ID),
                )

            if binding_key not in existing.node_limits:
                node_limit = node_limits_raw.get(node.key)
                if not _is_positive_int(node_limit):
                    node_limit = 1
                conn.execute(
                    """
                    insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit)
                    values (?, ?, ?, ?)
                    """,
                    (workspace_id, definition.key, node.key, int(node_limit)),  # type: ignore[arg-type]
                )
        elif pi_supported and needs_pi and binding_key not in existing.bindings:
            conn.execute(
                """
                insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id)
                values (?, ?, ?, ?)
                """,
                (workspace_id, definition.key, node.key, _DEFAULT_PI_EXECUTOR_ID),
            )

    executor_configuration.mark_workspace_executor_configuration_authoritative(conn, workspace_id)


def finalize_legacy_executor_schema(
    conn: sqlite3.Connection,
    definitions: list[WorkflowDefinition],
    executors: dict[str, ExecutorConfig],
    *,
    dry_run: bool = False,
    backup_path: Path | None = None,
) -> MigrationReport:
    """One-time finalizer that translates legacy Workspace Agent/Pipeline config into Executor allocations.

    When ``dry_run`` is True, no writes are performed and a clean preflight returns an empty
    :class:`MigrationReport`.  A non-empty report always raises :class:`MigrationBlockedError`.
    """
    if _version_is_recorded(conn, _MIGRATION_VERSION):
        logger.debug(
            "Migration %d already recorded; skipping legacy finalization", _MIGRATION_VERSION
        )
        return MigrationReport(
            migration_version=_MIGRATION_VERSION,
            migration_name=_MIGRATION_NAME,
            issues=(),
        )

    definitions_by_key = {definition.key: definition for definition in definitions}
    workspaces = _collect_legacy_data(conn)

    issues: list[MigrationIssue] = []
    for workspace_id, workspace in workspaces.items():
        issues.extend(_preflight_workspace(workspace_id, workspace, definitions_by_key, executors))

    if issues:
        report = MigrationReport(
            migration_version=_MIGRATION_VERSION,
            migration_name=_MIGRATION_NAME,
            issues=tuple(issues),
        )
        raise_blocked(report)

    if dry_run:
        return MigrationReport(
            migration_version=_MIGRATION_VERSION,
            migration_name=_MIGRATION_NAME,
            issues=(),
        )

    if backup_path is not None:
        backup_sqlite_connection(conn, backup_path)

    for workspace_id, workspace in workspaces.items():
        definition = definitions_by_key.get(workspace["default_workflow_key"])
        if definition is None:
            continue
        existing = collect_existing_configuration(conn, workspace_id)
        _materialize_workspace(conn, workspace_id, workspace, definition, executors, existing)

    V005_MIGRATION.apply(conn)
    conn.execute(
        "insert into schema_migrations(version, name) values (?, ?)",
        (_MIGRATION_VERSION, _MIGRATION_NAME),
    )

    return MigrationReport(
        migration_version=_MIGRATION_VERSION,
        migration_name=_MIGRATION_NAME,
        issues=(),
    )
