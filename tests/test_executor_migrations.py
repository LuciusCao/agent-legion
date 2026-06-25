import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS
from server.app.db.migrations.runner import Migration, run_migrations
from server.app.db.schema import init_db

EXPECTED_VERSIONS = [m.version for m in MIGRATIONS]
EXPECTED_VERSIONS_WITH_V005 = sorted(EXPECTED_VERSIONS + [5])
EXPECTED_TABLES = {
    "schema_migrations",
    "workspace_executor_allocations",
    "workspace_node_bindings",
    "workspace_node_limits",
    "workspace_executor_bootstrap_state",
    "executor_leases",
}

# V005 is applied by the one-time legacy finalizer, not by init_db/run_migrations.
# Tests for the destructive V005 cleanup live in test_executor_legacy_finalization.py.


def test_empty_database_migrates_to_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()

        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == EXPECTED_VERSIONS
        assert conn.execute("pragma foreign_key_check").fetchall() == []

    assert list(tmp_path.glob("empty-before-v007-*.sqlite")) == []


def test_v007_creates_pre_migration_backup_for_pipeline_columns(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        conn.execute("insert into workspaces(id, name) values ('ws1', 'Test Workspace')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_comprehension_info', 'question', 'Q1')"
        )
        conn.execute("delete from schema_migrations where version=7")
        for table, new_name, old_name in (
            ("workspaces", "default_workflow_key", "default_pipeline_key"),
            ("job_batches", "workflow_key", "pipeline_key"),
            ("jobs", "workflow_key", "pipeline_key"),
            ("workspace_node_bindings", "workflow_key", "pipeline_key"),
            ("workspace_node_limits", "workflow_key", "pipeline_key"),
            ("executor_leases", "workflow_key", "pipeline_key"),
        ):
            conn.execute(f"alter table {table} rename column {new_name} to {old_name}")

    init_db(path)

    backups = list(tmp_path.glob("upgrade-before-v007-*.sqlite"))
    assert len(backups) == 1
    with closing(connect_sqlite(backups[0])) as backup, backup:
        backup_columns = {
            row["name"] for row in backup.execute("pragma table_info(jobs)").fetchall()
        }
        assert "pipeline_key" in backup_columns
        assert "workflow_key" not in backup_columns
        assert backup.execute("select pipeline_key from jobs where id='job1'").fetchone()[0] == (
            "question_comprehension_info"
        )

    with closing(connect_sqlite(path)) as conn, conn:
        live_columns = {row["name"] for row in conn.execute("pragma table_info(jobs)").fetchall()}
        assert "workflow_key" in live_columns
        assert "pipeline_key" not in live_columns


def test_legacy_database_migrates_to_latest_version(tmp_path: Path) -> None:
    """A pre-Phase-3 database with workspace_agent_assignments is upgraded cleanly."""
    path = tmp_path / "legacy.sqlite"
    with closing(connect_sqlite(path)) as conn, conn:
        conn.executescript(
            """
            create table workspaces (
              id text primary key,
              name text not null,
              description text not null default '',
              default_workflow_key text not null default 'question_comprehension_info',
              cms_config_json text not null default '{}',
              resource_config_json text not null default '{}',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              default_entity text not null default 'question',
              intake_config_json text not null default '{}',
              pipeline_config_json text not null default '{}'
            );
            create table job_batches (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_kind text not null,
              source_payload_json text not null default '{}',
              status text not null default 'created',
              created_count integer not null default 0,
              error_message text not null default '',
              created_at text not null default current_timestamp
            );
            create table jobs (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_type text not null,
              source_id text not null,
              batch_id text not null default '',
              title text not null default '',
              status text not null default 'queued',
              storage_dir text not null default '',
              error_message text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp
            );
            create table job_nodes (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null default 'pending',
              stale_reason text not null default '',
              error_message text not null default '',
              started_at text,
              finished_at text,
              unique(job_id, node_key)
            );
            create table node_runs (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null,
              started_at text not null default current_timestamp,
              finished_at text,
              command_json text not null default '[]',
              exit_code integer,
              log_path text not null default '',
              error_message text not null default '',
              run_dir text not null default '',
              session_dir text not null default ''
            );
            create table workspace_agent_assignments (
              workspace_id text not null,
              agent_id text not null,
              concurrency_limit integer not null default 1,
              primary key (workspace_id, agent_id)
            );
            insert into workspaces(id, name) values ('ws1', 'Legacy Workspace');
            insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit)
            values ('ws1', 'agent_a', 3);
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        legacy_row = conn.execute(
            "select * from workspace_agent_assignments where workspace_id = 'ws1'"
        ).fetchone()

        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == EXPECTED_VERSIONS
        assert legacy_row is not None
        assert legacy_row["agent_id"] == "agent_a"
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_v004_migration_preserves_existing_data(tmp_path: Path) -> None:
    """A pre-V004 database with data survives the rebuild with no column misalignment."""
    path = tmp_path / "v004_preserve.sqlite"
    with closing(connect_sqlite(path)) as conn, conn:
        conn.executescript(
            """
            create table workspaces (
              id text primary key,
              name text not null,
              description text not null default '',
              default_workflow_key text not null default 'question_comprehension_info',
              cms_config_json text not null default '{}',
              resource_config_json text not null default '{}',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              default_entity text not null default 'question',
              intake_config_json text not null default '{}',
              pipeline_config_json text not null default '{}'
            );
            create table job_batches (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_kind text not null,
              source_payload_json text not null default '{}',
              status text not null default 'created',
              created_count integer not null default 0,
              error_message text not null default '',
              created_at text not null default current_timestamp
            );
            create table jobs (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_type text not null,
              source_id text not null,
              batch_id text not null default '',
              title text not null default '',
              status text not null default 'queued',
              storage_dir text not null default '',
              error_message text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              stem text not null default ''
            );
            create table job_nodes (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null default 'pending',
              stale_reason text not null default '',
              error_message text not null default '',
              started_at text,
              finished_at text,
              unique(job_id, node_key)
            );
            create table node_runs (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null,
              started_at text not null default current_timestamp,
              finished_at text,
              command_json text not null default '[]',
              exit_code integer,
              log_path text not null default '',
              error_message text not null default '',
              run_dir text not null default '',
              session_dir text not null default ''
            );
            insert into workspaces(id, name) values ('ws1', 'Legacy Workspace');
            insert into job_batches(
              id, workspace_id, workflow_key, source_kind, source_payload_json,
              status, created_count, error_message, created_at
            ) values (
              'batch1', 'ws1', 'question_comprehension_info', 'question', '{}',
              'created', 5, '', '2024-01-01 09:00:00'
            );
            insert into jobs(
              id, workspace_id, workflow_key, source_type, source_id, batch_id,
              title, status, storage_dir, error_message, created_at, updated_at, stem
            ) values (
              'job1', 'ws1', 'question_comprehension_info', 'question', 'q1', 'batch1',
              'Test Job', 'running', '/tmp/job1', '',
              '2024-01-01 10:00:00', '2024-01-01 12:00:00', 'job-stem-value'
            );
            insert into job_nodes(
              job_id, node_key, status, stale_reason, error_message,
              started_at, finished_at
            ) values (
              'job1', 'extract_keywords', 'completed', '', '',
              '2024-01-01 10:05:00', '2024-01-01 10:10:00'
            );
            insert into node_runs(
              job_id, node_key, status, started_at, finished_at, command_json,
              exit_code, log_path, error_message, run_dir, session_dir
            ) values (
              'job1', 'extract_keywords', 'completed',
              '2024-01-01 10:05:00', '2024-01-01 10:10:00', '[]', 0,
              '/tmp/log', '', '/tmp/run', '/tmp/session'
            );
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        batch = conn.execute("select * from job_batches where id = 'batch1'").fetchone()
        assert batch is not None
        assert batch["workspace_id"] == "ws1"
        assert batch["workflow_key"] == "question_comprehension_info"
        assert batch["source_kind"] == "question"
        assert batch["created_count"] == 5
        assert batch["created_at"] == "2024-01-01 09:00:00"

        job = conn.execute("select * from jobs where id = 'job1'").fetchone()
        assert job is not None
        assert job["workspace_id"] == "ws1"
        assert job["workflow_key"] == "question_comprehension_info"
        assert job["source_type"] == "question"
        assert job["source_id"] == "q1"
        assert job["batch_id"] == "batch1"
        assert job["title"] == "Test Job"
        assert job["status"] == "running"
        assert job["storage_dir"] == "/tmp/job1"
        assert job["error_message"] == ""
        assert job["created_at"] == "2024-01-01 10:00:00"
        assert job["updated_at"] == "2024-01-01 12:00:00"
        assert job["stem"] == "job-stem-value"

        node = conn.execute("select * from job_nodes where job_id = 'job1'").fetchone()
        assert node is not None
        assert node["node_key"] == "extract_keywords"
        assert node["status"] == "completed"
        assert node["started_at"] == "2024-01-01 10:05:00"
        assert node["finished_at"] == "2024-01-01 10:10:00"

        run = conn.execute("select * from node_runs where job_id = 'job1'").fetchone()
        assert run is not None
        assert run["node_key"] == "extract_keywords"
        assert run["status"] == "completed"
        assert run["exit_code"] == 0
        assert run["log_path"] == "/tmp/log"
        assert run["run_dir"] == "/tmp/run"
        assert run["session_dir"] == "/tmp/session"

        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_v004_creates_required_indexes_and_foreign_keys(tmp_path: Path) -> None:
    """The V004 rebuild installs the required FK relationships and indexes."""
    path = tmp_path / "v004_fks.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        indexes = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }
        assert "idx_job_batches_workspace" in indexes
        assert "idx_jobs_workflow_status" in indexes
        assert "idx_jobs_workflow_source" in indexes
        assert "idx_jobs_workspace_workflow_status" in indexes
        assert "idx_jobs_workspace_workflow_source" in indexes
        assert "idx_job_nodes_job_status" in indexes
        assert "idx_node_runs_job_id" in indexes
        assert "idx_executor_leases_global_active" in indexes
        assert "idx_executor_leases_workspace_active" in indexes
        assert "idx_executor_leases_workflow_node_active" in indexes

        relationships = {
            (table, row["from"], row["table"])
            for table in ("job_batches", "jobs", "job_nodes", "node_runs", "executor_leases")
            for row in conn.execute(f"pragma foreign_key_list('{table}')").fetchall()
        }
        assert relationships == {
            ("job_batches", "workspace_id", "workspaces"),
            ("jobs", "workspace_id", "workspaces"),
            ("job_nodes", "job_id", "jobs"),
            ("node_runs", "job_id", "jobs"),
            ("executor_leases", "workspace_id", "workspaces"),
            ("executor_leases", "job_id", "jobs"),
            ("executor_leases", "node_run_id", "node_runs"),
        }


def test_v004_foreign_key_cascades(tmp_path: Path) -> None:
    """Deleting a workspace cascades to batches/jobs/leases; deleting a node_run cascades to its lease."""
    path = tmp_path / "v004_cascade.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        conn.execute("insert into workspaces(id, name) values ('ws1', 'Workspace One')")
        conn.execute(
            "insert into job_batches(id, workspace_id, workflow_key, source_kind) "
            "values ('batch1', 'ws1', 'question_comprehension_info', 'mixed')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_comprehension_info', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job1', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status) values ('job1', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "workflow_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease1', 'exec1', 'exec_a', 'ws1', 'job1', 'question_comprehension_info', "
            "'node_a', 1, 'active', '2024-01-01 10:00:00', '2024-01-01 10:00:00', "
            "'2024-01-01 11:00:00')"
        )

        conn.execute("delete from node_runs where id = 1")
        assert conn.execute("select count(*) from executor_leases").fetchone()[0] == 0

        conn.execute("delete from workspaces where id = 'ws1'")
        assert conn.execute("select count(*) from job_batches").fetchone()[0] == 0
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0
        assert conn.execute("select count(*) from job_nodes").fetchone()[0] == 0
        assert conn.execute("select count(*) from node_runs").fetchone()[0] == 0
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_executor_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"
    init_db(path)
    init_db(path)
    with closing(connect_sqlite(path)) as conn, conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == EXPECTED_VERSIONS
        assert conn.execute("pragma foreign_key_check").fetchall() == []

        # V006 check constraint accepts until_node and rejects the old targeted value.
        conn.execute(
            "insert into workspaces(id, name) values ('constraint_ws', 'Constraint Workspace')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, execution_mode) "
            "values ('job_ok', 'constraint_ws', 'question_comprehension_info', 'question_id', 'Q1', 'until_node')"
        )
        assert (
            conn.execute("select execution_mode from jobs where id='job_ok'").fetchone()[0]
            == "until_node"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, execution_mode) "
                "values ('job_bad', 'constraint_ws', 'question_comprehension_info', 'question_id', 'Q2', 'targeted')"
            )


def test_v006_is_compatible_with_later_v005_finalizer(tmp_path: Path) -> None:
    """init_db records V006; a later one-time V005 finalizer does not invalidate it."""
    path = tmp_path / "v006_then_v005.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert [row["version"] for row in versions] == EXPECTED_VERSIONS

    # Simulate the destructive V005 finalizer recording its version manually.
    with closing(connect_sqlite(path)) as conn, conn:
        conn.execute(
            "insert into schema_migrations(version, name) values (?, ?)",
            (5, "legacy_workspace_executor_finalization"),
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert [row["version"] for row in versions] == EXPECTED_VERSIONS_WITH_V005
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_v013_adds_node_runs_skill_version_column(tmp_path: Path) -> None:
    """A pre-V013 database gets the skill_version column added to node_runs."""
    path = tmp_path / "v013_upgrade.sqlite"
    with closing(connect_sqlite(path)) as conn, conn:
        conn.executescript(
            """
            create table schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null default current_timestamp
            );
            insert into schema_migrations(version, name) values (1, 'executor_core');
            insert into schema_migrations(version, name) values (2, 'executor_bootstrap_state');
            insert into schema_migrations(version, name) values (3, 'legacy_columns');
            insert into schema_migrations(version, name) values (4, 'workspace_dag_foreign_keys');
            insert into schema_migrations(version, name) values (6, 'job_execution_control');
            insert into schema_migrations(version, name) values (7, 'rename_pipeline_to_workflow');
            insert into schema_migrations(version, name) values (8, 'job_node_created_at');
            insert into schema_migrations(version, name) values (9, 'relative_path_storage');
            insert into schema_migrations(version, name) values (10, 'remove_default_workspace');
            insert into schema_migrations(version, name) values (11, 'remove_workspace_id_defaults');
            insert into schema_migrations(version, name) values (12, 'workspace_packages');
            create table node_runs (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null,
              started_at text not null default current_timestamp,
              finished_at text,
              command_json text not null default '[]',
              exit_code integer,
              log_path text not null default '',
              error_message text not null default '',
              run_dir text not null default '',
              session_dir text not null default ''
            );
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        columns = {
            row["name"]: row for row in conn.execute("pragma table_info(node_runs)").fetchall()
        }
        assert "skill_version" in columns
        assert columns["skill_version"]["notnull"] == 1
        assert columns["skill_version"]["dflt_value"] == "''"


def test_failed_migration_is_fully_rolled_back(tmp_path: Path) -> None:
    """A migration that fails partway through leaves no version row or partial schema."""
    path = tmp_path / "rollback.sqlite"
    conn = connect_sqlite(path)

    def _failing_apply(conn) -> None:
        conn.execute("create table partial_table (id integer primary key)")
        conn.execute("create table bad_table (id integer primary key,")

    failing = Migration(version=2, name="failing", apply=_failing_apply)

    try:
        run_migrations(conn, [failing])
    except Exception:
        pass
    finally:
        conn.close()

    with closing(connect_sqlite(path)) as conn, conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert "partial_table" not in tables
        assert [row["version"] for row in versions] == []
