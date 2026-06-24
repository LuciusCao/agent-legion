from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS
from server.app.db.schema import init_db

EXPECTED_VERSIONS = [m.version for m in MIGRATIONS]


def test_v008_adds_created_at_to_existing_job_nodes(tmp_path: Path) -> None:
    """A database created before V008 gets created_at backfilled on job_nodes."""
    path = tmp_path / "pre_v008.sqlite"
    with closing(connect_sqlite(path)) as conn, conn:
        conn.executescript(
            """
            create table workspaces (
              id text primary key,
              name text not null,
              description text not null default '',
              default_workflow_key text not null default 'question_content',
              cms_config_json text not null default '{}',
              resource_config_json text not null default '{}',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              default_entity text not null default 'question',
              intake_config_json text not null default '{}'
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
              execution_mode text not null default 'full',
              target_node_key text,
              execution_paused integer not null default 0,
              pause_reason text not null default ''
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
            insert into workspaces(id, name) values ('ws1', 'Workspace One');
            insert into jobs(id, workspace_id, workflow_key, source_type, source_id)
              values ('job1', 'ws1', 'question_content', 'question', 'Q1');
            insert into job_nodes(job_id, node_key, status, started_at, finished_at)
              values ('job1', 'completed_node', 'completed', '2026-06-09T00:00:00Z', '2026-06-09T00:00:10Z');
            insert into job_nodes(job_id, node_key, status)
              values ('job1', 'pending_node', 'pending');
            """
        )

    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        versions = [
            row["version"]
            for row in conn.execute(
                "select version from schema_migrations order by version"
            ).fetchall()
        ]
        assert versions == EXPECTED_VERSIONS

        completed = conn.execute(
            "select created_at from job_nodes where node_key='completed_node'"
        ).fetchone()
        assert completed is not None
        assert completed["created_at"] == "2026-06-09T00:00:00Z"

        pending = conn.execute(
            "select created_at from job_nodes where node_key='pending_node'"
        ).fetchone()
        assert pending is not None
        assert len(pending["created_at"]) >= 19


def test_v008_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v008_idempotent.sqlite"
    init_db(path)
    init_db(path)

    with closing(connect_sqlite(path)) as conn, conn:
        versions = [
            row["version"]
            for row in conn.execute(
                "select version from schema_migrations order by version"
            ).fetchall()
        ]
        assert versions == EXPECTED_VERSIONS
        cols = {row["name"] for row in conn.execute("pragma table_info(job_nodes)").fetchall()}
        assert "created_at" in cols
