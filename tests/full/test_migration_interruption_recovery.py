"""Full-gate migration interruption/reopen drills.

These scenarios build a pre-V004 database, fail a migration at an internal phase
boundary, close the connection, reopen through ``init_db()``, and assert that the
database is either in its original intact state or has completed cleanly.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS, run_migrations
from server.app.db.schema import init_db
from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs import JobQueries
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import ensure_legacy_workspace_tables
from tests.test_workspace_dag_fk_migration import _create_pre_v004_database

pytestmark = pytest.mark.full_gate


def _foreign_key_relationships(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    relationships: set[tuple[str, str, str]] = set()
    for table in ("job_batches", "jobs", "job_nodes", "node_runs", "executor_leases"):
        for row in conn.execute(f"pragma foreign_key_list('{table}')").fetchall():
            relationships.add((table, row["from"], row["table"]))
    return relationships


def _expected_fk_relationships() -> set[tuple[str, str, str]]:
    return {
        ("job_batches", "workspace_id", "workspaces"),
        ("jobs", "workspace_id", "workspaces"),
        ("job_nodes", "job_id", "jobs"),
        ("node_runs", "job_id", "jobs"),
        ("executor_leases", "workspace_id", "workspaces"),
        ("executor_leases", "job_id", "jobs"),
        ("executor_leases", "node_run_id", "node_runs"),
    }


@pytest.mark.parametrize(
    "interrupt_phase",
    [
        "v004:copy:job_batches",
        "v004:drop:job_batches",
        "v004:rename:job_batches__v004",
    ],
)
def test_v004_interruption_at_phase_boundary_recovers(tmp_path: Path, interrupt_phase: str) -> None:
    path = tmp_path / "v004_interrupt.sqlite"
    _create_pre_v004_database(path)

    conn = connect_sqlite(path)
    with conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_content', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job1', 'extract', 'pending')"
        )
    conn.close()

    def hook(phase: str) -> None:
        if phase == interrupt_phase:
            raise RuntimeError(f"interrupted at {phase}")

    conn = connect_sqlite(path)
    with pytest.raises(RuntimeError, match=f"interrupted at {interrupt_phase}"):
        run_migrations(conn, MIGRATIONS, _phase_hook=hook)
    conn.close()

    # Reopen normally.  init_db() reruns migrations from a clean state.
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 1
        assert conn.execute("select count(*) from job_nodes").fetchone()[0] == 1
        versions = [
            row["version"]
            for row in conn.execute(
                "select version from schema_migrations order by version"
            ).fetchall()
        ]
        assert versions == [1, 2, 3, 4, 6, 7]
        assert _foreign_key_relationships(conn) == _expected_fk_relationships()
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def _sample_executors() -> dict[str, ExecutorConfig]:
    return {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=2,
            capabilities={
                "local_a": LocalCapabilityConfig(handler="reading_analysis.local_a"),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=8,
            capabilities={
                "pi_a": PiCapabilityConfig(
                    skill="reading_analysis/pi_a",
                    tools=("read", "write", "bash"),
                )
            },
        ),
    }


def _sample_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(key="local_a", label="Local A", capability="local_a"),
            "pi_a": WorkflowNode(key="pi_a", label="Pi A", capability="pi_a"),
        },
    )


def _question_comprehension_info_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="question_comprehension_info",
        label="Question Comprehension Info",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(key="local_a", label="Local A", capability="local_a"),
        },
    )


def test_v005_finalizer_interruption_before_commit_retains_backup_and_reruns(
    tmp_path: Path,
) -> None:
    """V005's independent finalizer can be interrupted before commit and rerun safely."""
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir)
    ensure_legacy_workspace_tables(queries)

    workspace_id = queries.create_workspace(name="Legacy", default_workflow_key="reading_analysis")[
        "id"
    ]
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            ('{"local": 3}', workspace_id),
        )
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) "
            "values (?, 'pi', 2)",
            (workspace_id,),
        )

    backup_path = tmp_path / "v005-backup.sqlite"

    def block_schema_history_insert(action: int, *args: object) -> int:
        if action == sqlite3.SQLITE_INSERT and args[0] == "schema_migrations":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with pytest.raises(sqlite3.DatabaseError), queries.connect() as conn:
        conn.set_authorizer(block_schema_history_insert)
        finalize_legacy_executor_schema(
            conn,
            [_sample_workflow(), _question_comprehension_info_workflow()],
            _sample_executors(),
            backup_path=backup_path,
        )

    # Backup was written before the destructive work and survives the rollback.
    assert backup_path.is_file()

    with queries._connect_read() as conn:
        # Legacy tables and column are intact because the transaction rolled back.
        assert (
            conn.execute(
                "select 1 from sqlite_master where type='table' and name='workspace_agent_assignments'"
            ).fetchone()
            is not None
        )
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)").fetchall()}
        assert "pipeline_config_json" in columns
        version = conn.execute("select 1 from schema_migrations where version = 5").fetchone()
        assert version is None

    # Reopen through a fresh init_db and rerun the finalizer.
    queries2 = JobQueries(db_path, jobs_dir)
    with queries2.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn,
            [_sample_workflow(), _question_comprehension_info_workflow()],
            _sample_executors(),
            backup_path=backup_path,
        )

    assert report.issues == ()

    with queries2._connect_read() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)").fetchall()}
        assert "pipeline_config_json" not in columns
        version = conn.execute("select name from schema_migrations where version = 5").fetchone()
        assert version is not None and version[0] == "remove_legacy_executor_paths"
        allocations = conn.execute(
            "select executor_id, concurrency_limit from workspace_executor_allocations "
            "where workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        assert {row["executor_id"]: row["concurrency_limit"] for row in allocations} == {
            "local-default": 3,
            "pi-default": 2,
        }
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []

    # Original backup from the interrupted run is still present.
    assert backup_path.is_file()
