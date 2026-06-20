from __future__ import annotations

import sqlite3

import pytest

from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs.queries import JobQueries
from tests.executors.legacy.helpers import (
    _fetch_all_allocations,
    _insert_legacy_agent_assignment,
    _sample_executors,
    _sample_workflows,
    _set_workflow_config,
    _table_exists,
)


def test_finalizer_interruption_before_commit_retains_backup_and_reruns(
    queries: JobQueries,
) -> None:
    """A crash before the finalizer commits leaves the backup and allows a safe rerun."""
    workspace_id = queries.create_workspace(name="Legacy", default_workflow_key="reading_analysis")[
        "id"
    ]
    _set_workflow_config(queries, workspace_id, {"local": 3})
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 2)

    backup_path = queries.path.parent / "v005-backup.sqlite"

    def block_schema_history_insert(action: int, *args: object) -> int:
        if action == sqlite3.SQLITE_INSERT and args[0] == "schema_migrations":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with pytest.raises(sqlite3.DatabaseError), queries.connect() as conn:
        conn.set_authorizer(block_schema_history_insert)
        finalize_legacy_executor_schema(
            conn, _sample_workflows(), _sample_executors(), backup_path=backup_path
        )

    assert backup_path.is_file()
    assert _table_exists(queries, "workspace_agent_assignments")

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn, _sample_workflows(), _sample_executors(), backup_path=backup_path
        )

    assert report.issues == ()
    assert not _table_exists(queries, "workspace_agent_assignments")

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations == {"local-default": 3, "pi-default": 2}
    assert backup_path.is_file()
