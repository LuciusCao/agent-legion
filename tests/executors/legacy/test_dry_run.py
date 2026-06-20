from __future__ import annotations

import sqlite3

import pytest

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.report import MigrationBlockedError
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs.queries import JobQueries
from tests.executors.legacy.helpers import (
    _fetch_all_allocations,
    _insert_legacy_agent_assignment,
    _list_legacy_agent_assignments,
    _sample_executors,
    _sample_workflows,
    _table_exists,
)


def test_dry_run_returns_report_without_writing(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Dry Run",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn, _sample_workflows(), _sample_executors(), dry_run=True
        )

    assert report.issues == ()
    assert _fetch_all_allocations(queries) == []
    assert _table_exists(queries, "workspace_agent_assignments")


def test_dry_run_raises_blocked_error_and_leaves_legacy_data(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Blocked Dry Run",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "bad-agent", 2)

    with pytest.raises(MigrationBlockedError), queries.connect() as conn:
        finalize_legacy_executor_schema(
            conn, _sample_workflows(), _sample_executors(), dry_run=True
        )

    assert _table_exists(queries, "workspace_agent_assignments")
    rows = _list_legacy_agent_assignments(queries, workspace_id)
    assert any(row["agent_id"] == "bad-agent" for row in rows)


def test_v005_rolls_back_when_drop_column_is_not_supported(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unsupported SQLite",
        default_workflow_key="reading_analysis",
    )["id"]
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_executor_bootstrap_state(workspace_id) values (?)",
            (workspace_id,),
        )

    with pytest.raises(MigrationError), queries.connect() as conn:
        conn.set_authorizer(
            lambda action, *_args: (
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK
            )
        )
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    assert _table_exists(queries, "workspace_agent_assignments")
    assert _table_exists(queries, "workspace_executor_bootstrap_state")
    with queries._connect_read() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)")}
        version = conn.execute("select 1 from schema_migrations where version = 5").fetchone()
        allocation = conn.execute(
            "select 1 from workspace_executor_allocations where workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    assert "pipeline_config_json" in columns
    assert version is None
    assert allocation is None
