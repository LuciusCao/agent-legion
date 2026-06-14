import json
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS, run_migrations
from server.app.db.migrations.report import MigrationIssue, MigrationReport
from server.app.db.migrations.v004_workspace_dag_foreign_keys import (
    _MIGRATION_NAME,
    _MIGRATION_VERSION,
)
from server.app.db.schema import init_db


def _create_pre_v004_database(path: Path) -> None:
    conn = connect_sqlite(path)
    with conn:
        conn.executescript(
            """
            create table workspaces (
              id text primary key,
              name text not null,
              description text not null default '',
              default_pipeline_key text not null default 'question_content',
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
              pipeline_key text not null,
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
              pipeline_key text not null,
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
            create table executor_leases (
              id text primary key,
              execution_id text not null unique,
              executor_id text not null,
              workspace_id text not null,
              job_id text not null,
              pipeline_key text not null,
              node_key text not null,
              node_run_id integer not null,
              status text not null check(status in ('active', 'released', 'expired')),
              acquired_at text not null,
              heartbeat_at text not null,
              expires_at text not null,
              foreign key(workspace_id) references workspaces(id) on delete cascade,
              foreign key(job_id) references jobs(id) on delete cascade,
              foreign key(node_run_id) references node_runs(id) on delete cascade
            );
            create table schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null default current_timestamp
            );
            insert into schema_migrations(version, name) values (1, 'executor_core');
            insert into schema_migrations(version, name) values (2, 'executor_bootstrap_state');
            insert into schema_migrations(version, name) values (3, 'legacy_columns');
            insert into workspaces(id, name) values ('ws1', 'Workspace One');
            """
        )
    conn.close()


def _foreign_key_relationships(conn) -> set[tuple[str, str, str]]:
    relationships: set[tuple[str, str, str]] = set()
    for table in ("job_batches", "jobs", "job_nodes", "node_runs", "executor_leases"):
        for row in conn.execute(f"pragma foreign_key_list('{table}')").fetchall():
            relationships.add((table, row["from"], row["table"]))
    return relationships


def test_v004_blocked_by_orphan_rows_and_leaves_data_intact(tmp_path: Path) -> None:
    path = tmp_path / "orphans.sqlite"
    _create_pre_v004_database(path)

    conn = connect_sqlite(path)
    with conn:
        # Disable FK enforcement so we can insert intentional orphan rows.
        conn.execute("pragma foreign_keys = off")
        conn.execute(
            "insert into job_batches(id, workspace_id, pipeline_key, source_kind) "
            "values ('batch1', 'missing_ws', 'question_content', 'mixed')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id) "
            "values ('job1', 'missing_ws', 'question_content', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id) "
            "values ('job2', 'ws1', 'question_content', 'question_id', 'Q2')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job2', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status) values ('job2', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease1', 'exec1', 'exec_a', 'ws1', 'job2', 'question_content', 'node_a', "
            "1, 'active', '2024-01-01 10:00:00', '2024-01-01 10:00:00', '2024-01-01 11:00:00')"
        )
        # Intentional orphans on executor_leases.
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease_bad_ws', 'exec_bad_ws', 'exec_a', 'missing_ws', 'job2', "
            "'question_content', 'node_a', 1, 'active', '2024-01-01 10:00:00', "
            "'2024-01-01 10:00:00', '2024-01-01 11:00:00')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease_bad_job', 'exec_bad_job', 'exec_a', 'ws1', 'missing_job', "
            "'question_content', 'node_a', 1, 'active', '2024-01-01 10:00:00', "
            "'2024-01-01 10:00:00', '2024-01-01 11:00:00')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease_bad_run', 'exec_bad_run', 'exec_a', 'ws1', 'job2', "
            "'question_content', 'node_a', 999, 'active', '2024-01-01 10:00:00', "
            "'2024-01-01 10:00:00', '2024-01-01 11:00:00')"
        )
        conn.execute("pragma foreign_keys = on")
    conn.close()

    from server.app.db.migrations.report import MigrationBlockedError

    with pytest.raises(MigrationBlockedError) as exc_info:
        init_db(path)

    report = exc_info.value.report
    assert report.migration_version == _MIGRATION_VERSION
    assert report.migration_name == _MIGRATION_NAME
    assert {issue.table for issue in report.issues} == {
        "job_batches",
        "jobs",
        "executor_leases",
    }
    assert any(
        issue.table == "job_batches" and issue.row_key == "batch1" for issue in report.issues
    )
    assert any(issue.table == "jobs" and issue.row_key == "job1" for issue in report.issues)
    assert any(
        issue.table == "executor_leases"
        and issue.row_key == "lease_bad_ws"
        and issue.constraint == "fk_executor_leases_workspace_id"
        for issue in report.issues
    )
    assert any(
        issue.table == "executor_leases"
        and issue.row_key == "lease_bad_job"
        and issue.constraint == "fk_executor_leases_job_id"
        for issue in report.issues
    )
    assert any(
        issue.table == "executor_leases"
        and issue.row_key == "lease_bad_run"
        and issue.constraint == "fk_executor_leases_node_run_id"
        for issue in report.issues
    )

    # Deterministic serialization: sorted by table/key/constraint, no JSON payloads.
    data = json.loads(report.to_json())
    assert data["migration_version"] == _MIGRATION_VERSION
    assert data["migration_name"] == _MIGRATION_NAME
    keys = [(issue["table"], issue["row_key"], issue["constraint"]) for issue in data["issues"]]
    assert keys == sorted(keys)
    for issue in data["issues"]:
        assert "source_payload_json" not in issue["message"]
        assert "command_json" not in issue["message"]

    # Source rows and version 4 must remain absent.
    with closing(connect_sqlite(path)) as conn, conn:
        assert conn.execute("select count(*) from job_batches").fetchone()[0] == 1
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 2
        assert conn.execute("select count(*) from job_nodes").fetchone()[0] == 1
        assert conn.execute("select count(*) from node_runs").fetchone()[0] == 1
        assert conn.execute("select count(*) from executor_leases").fetchone()[0] == 4
        versions = conn.execute("select version from schema_migrations").fetchall()
        assert 4 not in {row["version"] for row in versions}


def test_v004_rebuilds_tables_with_pre_existing_indexes(tmp_path: Path) -> None:
    """If the legacy tables already carry the named indexes, V004 must not fail
    with "index already exists" when it keeps the old copies of jobs/node_runs.
    """
    path = tmp_path / "v004_with_indexes.sqlite"
    _create_pre_v004_database(path)

    conn = connect_sqlite(path)
    with conn:
        # Indexes that may already exist on a real database from prior schema init.
        conn.execute("create index idx_jobs_pipeline_status on jobs(pipeline_key, status)")
        conn.execute("create index idx_jobs_source on jobs(pipeline_key, source_type, source_id)")
        conn.execute(
            "create index idx_jobs_workspace_pipeline_status on jobs(workspace_id, pipeline_key, status)"
        )
        conn.execute(
            "create index idx_jobs_workspace_source on jobs(workspace_id, pipeline_key, source_type, source_id)"
        )
        conn.execute("create index idx_job_nodes_job_status on job_nodes(job_id, status)")
        conn.execute("create index idx_node_runs_job_id on node_runs(job_id)")
        conn.execute(
            "create index idx_job_batches_workspace on job_batches(workspace_id, created_at)"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_content', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job1', 'extract', 'pending')"
        )
    conn.close()

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        # All expected indexes still exist and are attached to the rebuilt tables,
        # not the temporary old copies.
        rows = {
            row["name"]: row["tbl_name"]
            for row in conn.execute(
                "select name, tbl_name from sqlite_master where type = 'index'"
            ).fetchall()
        }
        assert rows["idx_jobs_pipeline_status"] == "jobs"
        assert rows["idx_jobs_source"] == "jobs"
        assert rows["idx_jobs_workspace_pipeline_status"] == "jobs"
        assert rows["idx_jobs_workspace_source"] == "jobs"
        assert rows["idx_job_nodes_job_status"] == "job_nodes"
        assert rows["idx_node_runs_job_id"] == "node_runs"
        assert rows["idx_job_batches_workspace"] == "job_batches"
        assert "jobs__v004_old" not in {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type = 'table'")
        }
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 1


def test_v004_preserves_data_indexes_and_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "v004_fk.sqlite"
    _create_pre_v004_database(path)

    conn = connect_sqlite(path)
    with conn:
        conn.execute(
            "insert into job_batches(id, workspace_id, pipeline_key, source_kind, source_payload_json, "
            "status, created_count, error_message, created_at) "
            "values ('batch1', 'ws1', 'question_content', 'mixed', '{\"ids\":[1]}', "
            "'created', 3, '', '2024-01-01 09:00:00')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id, batch_id, "
            "title, status, storage_dir, error_message, created_at, updated_at, stem) "
            "values ('job1', 'ws1', 'question_content', 'question_id', 'Q1', 'batch1', "
            "'Title', 'running', '/tmp/job1', '', '2024-01-01 10:00:00', '2024-01-01 11:00:00', 'stem1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status, stale_reason, error_message, "
            "started_at, finished_at) values ('job1', 'extract', 'completed', '', '', "
            "'2024-01-01 10:05:00', '2024-01-01 10:10:00')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, started_at, finished_at, "
            "command_json, exit_code, log_path, error_message, run_dir, session_dir) "
            "values ('job1', 'extract', 'completed', '2024-01-01 10:05:00', "
            "'2024-01-01 10:10:00', '[]', 0, '/tmp/log', '', '/tmp/run', '/tmp/session')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease1', 'exec1', 'exec_a', 'ws1', 'job1', 'question_content', "
            "'extract', 1, 'active', '2024-01-01 10:00:00', '2024-01-01 10:00:00', "
            "'2024-01-01 11:00:00')"
        )
    conn.close()

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        # Data preserved exactly.
        batch = conn.execute("select * from job_batches where id = 'batch1'").fetchone()
        assert batch is not None
        assert batch["workspace_id"] == "ws1"
        assert batch["source_payload_json"] == '{"ids":[1]}'
        assert batch["created_count"] == 3
        assert batch["created_at"] == "2024-01-01 09:00:00"

        job = conn.execute("select * from jobs where id = 'job1'").fetchone()
        assert job is not None
        assert job["workspace_id"] == "ws1"
        assert job["batch_id"] == "batch1"
        assert job["stem"] == "stem1"
        assert job["updated_at"] == "2024-01-01 11:00:00"

        node = conn.execute("select * from job_nodes where job_id = 'job1'").fetchone()
        assert node is not None
        assert node["node_key"] == "extract"
        assert node["finished_at"] == "2024-01-01 10:10:00"

        run = conn.execute("select * from node_runs where job_id = 'job1'").fetchone()
        assert run is not None
        assert run["log_path"] == "/tmp/log"
        assert run["session_dir"] == "/tmp/session"

        lease = conn.execute("select * from executor_leases where id = 'lease1'").fetchone()
        assert lease is not None
        assert lease["workspace_id"] == "ws1"
        assert lease["job_id"] == "job1"
        assert lease["node_run_id"] == 1
        assert lease["status"] == "active"
        assert lease["expires_at"] == "2024-01-01 11:00:00"

        # Expected indexes exist.
        indexes = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }
        assert "idx_job_batches_workspace" in indexes
        assert "idx_jobs_pipeline_status" in indexes
        assert "idx_jobs_source" in indexes
        assert "idx_jobs_workspace_pipeline_status" in indexes
        assert "idx_jobs_workspace_source" in indexes
        assert "idx_job_nodes_job_status" in indexes
        assert "idx_node_runs_job_id" in indexes
        assert "idx_executor_leases_global_active" in indexes
        assert "idx_executor_leases_workspace_active" in indexes
        assert "idx_executor_leases_node_active" in indexes

        # Required FK relationships.
        assert _foreign_key_relationships(conn) == {
            ("job_batches", "workspace_id", "workspaces"),
            ("jobs", "workspace_id", "workspaces"),
            ("job_nodes", "job_id", "jobs"),
            ("node_runs", "job_id", "jobs"),
            ("executor_leases", "workspace_id", "workspaces"),
            ("executor_leases", "job_id", "jobs"),
            ("executor_leases", "node_run_id", "node_runs"),
        }

        # Cascades work.
        conn.execute("delete from node_runs where id = 1")
        assert conn.execute("select count(*) from executor_leases").fetchone()[0] == 0

        conn.execute("delete from workspaces where id = 'ws1'")
        assert conn.execute("select count(*) from job_batches").fetchone()[0] == 0
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0
        assert conn.execute("select count(*) from job_nodes").fetchone()[0] == 0
        assert conn.execute("select count(*) from node_runs").fetchone()[0] == 0

        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_v004_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v004_idempotent.sqlite"
    _create_pre_v004_database(path)
    init_db(path)
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert [row["version"] for row in versions] == [1, 2, 3, 4, 6]
        assert _foreign_key_relationships(conn) == {
            ("job_batches", "workspace_id", "workspaces"),
            ("jobs", "workspace_id", "workspaces"),
            ("job_nodes", "job_id", "jobs"),
            ("node_runs", "job_id", "jobs"),
            ("executor_leases", "workspace_id", "workspaces"),
            ("executor_leases", "job_id", "jobs"),
            ("executor_leases", "node_run_id", "node_runs"),
        }
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_migration_report_str_and_json_are_deterministic() -> None:
    issues = (
        MigrationIssue(
            table="jobs", row_key="job1", constraint="fk_jobs_workspace_id", message="missing"
        ),
        MigrationIssue(
            table="job_batches",
            row_key="batch1",
            constraint="fk_job_batches_workspace_id",
            message="missing",
        ),
        MigrationIssue(
            table="jobs", row_key="job1", constraint="fk_jobs_workspace_id", message="missing"
        ),
    )
    report = MigrationReport(
        migration_version=_MIGRATION_VERSION,
        migration_name=_MIGRATION_NAME,
        issues=issues,
    )

    text = str(report)
    assert text.startswith("Migration 4 (workspace_dag_foreign_keys) blocked")
    # Duplicates are preserved because issues tuple may contain them; sorting is stable.
    assert text.count("job1") == 2

    data = json.loads(report.to_json())
    assert data["migration_version"] == 4
    assert data["migration_name"] == "workspace_dag_foreign_keys"
    serialized_keys = [
        (issue["table"], issue["row_key"], issue["constraint"]) for issue in data["issues"]
    ]
    assert serialized_keys == sorted(serialized_keys)


def test_v004_interruption_after_copy_recovers_on_reopen(tmp_path: Path) -> None:
    """A hook-raised failure mid-V004 rolls back; reopening completes cleanly."""
    path = tmp_path / "v004_interrupt.sqlite"
    _create_pre_v004_database(path)

    conn = connect_sqlite(path)
    with conn:
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_content', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job1', 'extract', 'pending')"
        )
    conn.close()

    def hook(phase: str) -> None:
        if phase == "v004:copy:job_batches":
            raise RuntimeError("interrupted after copy")

    conn = connect_sqlite(path)
    with pytest.raises(RuntimeError, match="interrupted after copy"):
        run_migrations(conn, MIGRATIONS, _phase_hook=hook)
    conn.close()

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
        assert versions == [1, 2, 3, 4, 6]
        assert _foreign_key_relationships(conn) == {
            ("job_batches", "workspace_id", "workspaces"),
            ("jobs", "workspace_id", "workspaces"),
            ("job_nodes", "job_id", "jobs"),
            ("node_runs", "job_id", "jobs"),
            ("executor_leases", "workspace_id", "workspaces"),
            ("executor_leases", "job_id", "jobs"),
            ("executor_leases", "node_run_id", "node_runs"),
        }
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []
