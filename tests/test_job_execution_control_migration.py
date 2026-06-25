import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def _job_columns(conn):
    return {row["name"]: row for row in conn.execute("pragma table_info(jobs)").fetchall()}


def test_empty_database_applies_v006_job_execution_control(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        columns = _job_columns(conn)
        assert "execution_mode" in columns
        assert "target_node_key" in columns
        assert "execution_paused" in columns
        assert "pause_reason" in columns

        assert columns["execution_mode"]["notnull"] == 1
        assert columns["execution_mode"]["dflt_value"] == "'full'"
        assert columns["execution_paused"]["notnull"] == 1
        assert columns["execution_paused"]["dflt_value"] == "0"
        assert columns["pause_reason"]["notnull"] == 1
        assert columns["pause_reason"]["dflt_value"] == "''"
        assert columns["target_node_key"]["notnull"] == 0

        versions = conn.execute(
            "select version, name from schema_migrations where version = 6"
        ).fetchall()
        assert len(versions) == 1
        assert versions[0]["name"] == "job_execution_control"


def test_legacy_database_gains_execution_control_defaults(tmp_path: Path) -> None:
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
              intake_config_json text not null default '{}'
            );
            insert into workspaces(id, name) values ('ws1', 'Legacy Workspace');
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
              stem text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp
            );
            insert into jobs(
              id, workspace_id, workflow_key, source_type, source_id, batch_id,
              title, status, storage_dir, error_message, stem
            ) values (
              'job1', 'ws1', 'question_comprehension_info', 'question_id', 'Q1', 'batch1',
              'Legacy Job', 'queued', '/tmp/job1', '', 'stem-value'
            );
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        job = conn.execute("select * from jobs where id = 'job1'").fetchone()
        assert job is not None
        assert job["execution_mode"] == "full"
        assert job["target_node_key"] is None
        assert job["execution_paused"] == 0
        assert job["pause_reason"] == ""
        assert job["title"] == "Legacy Job"
        assert job["stem"] == "stem-value"

        versions = conn.execute(
            "select count(*) as cnt from schema_migrations where version = 6"
        ).fetchone()
        assert versions["cnt"] == 1


def test_v006_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "idempotent.sqlite"
    init_db(path)
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        columns = _job_columns(conn)
        assert "execution_mode" in columns
        assert "target_node_key" in columns
        assert "execution_paused" in columns
        assert "pause_reason" in columns

        versions = conn.execute(
            "select count(*) as cnt from schema_migrations where version = 6"
        ).fetchone()
        assert versions["cnt"] == 1


def test_v006_execution_mode_check_constraint(tmp_path: Path) -> None:
    path = tmp_path / "constraint.sqlite"
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
              intake_config_json text not null default '{}'
            );
            insert into workspaces(id, name) values ('ws1', 'Constraint Workspace');
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, execution_mode) "
            "values ('job1', 'ws1', 'question_comprehension_info', 'question_id', 'Q1', 'until_node')"
        )
        job = conn.execute("select * from jobs where id = 'job1'").fetchone()
        assert job is not None
        assert job["execution_mode"] == "until_node"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, execution_mode) "
                "values ('job2', 'ws1', 'question_comprehension_info', 'question_id', 'Q2', 'targeted')"
            )
