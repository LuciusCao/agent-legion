"""Full-gate backup/restore drill.

Creates a database with workspace/job/allocation rows, takes a backup, mutates the
live database, restores from the backup, and verifies that the original invariant
state returns while the previous database is preserved.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.app.executors.backup import (
    quiesce_sqlite_database,
    restore_sqlite_database,
)
from server.app.jobs import JobQueries

pytestmark = pytest.mark.full_gate


def _seed_database(db_path: Path) -> tuple[str, str, str]:
    jobs_dir = db_path.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir)
    workspace = queries.create_workspace(name="Drill", default_workflow_key="question_content")
    workspace_id = str(workspace["id"])
    batch = queries.create_batch(
        workflow_key="question_content",
        source_kind="mixed",
        source_payload={"ids": [1, 2]},
        workspace_id=workspace_id,
    )
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q1",
        batch_id=batch["id"],
        title="Drill Job",
        node_keys=["fetch", "understand"],
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    queries.update_workspace_configuration(
        workspace_id=workspace_id,
        name="Drill",
        description="",
        default_workflow_key="question_content",
        default_entity="question",
        resource_config={},
        intake_config={},
        executor_allocations=[
            {"executor_id": "local-default", "concurrency_limit": 5},
        ],
        node_bindings=[
            {
                "pipeline_key": "question_content",
                "node_key": "fetch",
                "executor_id": "local-default",
            },
        ],
        node_limits=[
            {"pipeline_key": "question_content", "node_key": "fetch", "concurrency_limit": 2},
        ],
    )
    return workspace_id, batch["id"], job_id


def _mutate_database(queries: JobQueries, workspace_id: str) -> None:
    # Make visible changes so we can prove the restore rolled them back.
    queries.update_workspace(
        workspace_id=workspace_id,
        name="Mutated",
    )
    with queries.connect() as conn:
        conn.execute("delete from jobs where workspace_id = ?", (workspace_id,))
        conn.execute(
            "delete from workspace_executor_allocations where workspace_id = ?", (workspace_id,)
        )


def test_full_backup_restore_drill_preserved_original_and_restored_invariants(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "drill.sqlite"
    workspace_id, batch_id, job_id = _seed_database(db_path)

    backup_path = tmp_path / "drill-backup.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
    finally:
        conn.close()

    # Mutate the live database after the backup.
    queries = JobQueries(db_path, db_path.parent / "jobs")
    _mutate_database(queries, workspace_id)

    with queries._connect_read() as conn:
        assert (
            conn.execute("select name from workspaces where id = ?", (workspace_id,)).fetchone()[0]
            == "Mutated"
        )
        assert (
            conn.execute(
                "select count(*) from jobs where workspace_id = ?", (workspace_id,)
            ).fetchone()[0]
            == 0
        )

    # Restore must be performed on a quiescent database.
    quiesce_sqlite_database(db_path)
    preserved, history = restore_sqlite_database(backup_path, db_path)

    assert preserved.is_file()
    assert preserved.parent == db_path.parent

    # Reopen through the normal repository path and prove original rows returned.
    restored = JobQueries(db_path, db_path.parent / "jobs")
    workspace = restored.get_workspace(workspace_id)
    assert workspace is not None
    assert workspace["name"] == "Drill"
    assert restored.get_job(job_id) is not None
    config = restored.get_workspace_executor_configuration(workspace_id)
    assert any(a["executor_id"] == "local-default" for a in config["allocations"])
    assert any(b["node_key"] == "fetch" for b in config["bindings"])

    with restored._connect_read() as conn:
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []
        assert any(row["version"] == 1 for row in history)

    # The preserved database contains the mutated state.
    preserved_conn = sqlite3.connect(preserved)
    preserved_conn.row_factory = sqlite3.Row
    try:
        assert preserved_conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        row = preserved_conn.execute(
            "select name from workspaces where id = ?", (workspace_id,)
        ).fetchone()
        assert row is not None
        assert row["name"] == "Mutated"
        assert (
            preserved_conn.execute(
                "select count(*) from jobs where workspace_id = ?", (workspace_id,)
            ).fetchone()[0]
            == 0
        )
    finally:
        preserved_conn.close()
