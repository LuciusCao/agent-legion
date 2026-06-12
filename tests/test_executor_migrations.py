from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations.runner import Migration, run_migrations
from server.app.db.schema import init_db

EXPECTED_TABLES = {
    "schema_migrations",
    "workspace_executor_allocations",
    "workspace_node_bindings",
    "workspace_node_limits",
    "workspace_executor_bootstrap_state",
    "executor_leases",
}


def test_empty_database_migrates_to_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    init_db(path)

    with connect_sqlite(path) as conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()

        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == [1, 2, 3, 4]
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_legacy_database_migrates_to_latest_version(tmp_path: Path) -> None:
    """A pre-Phase-3 database with workspace_agent_assignments is upgraded cleanly."""
    path = tmp_path / "legacy.sqlite"
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
    conn.close()

    init_db(path)

    with connect_sqlite(path) as conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        legacy_row = conn.execute(
            "select * from workspace_agent_assignments where workspace_id = 'ws1'"
        ).fetchone()

        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == [1, 2, 3, 4]
        assert legacy_row is not None
        assert legacy_row["agent_id"] == "agent_a"
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_executor_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"
    init_db(path)
    init_db(path)
    with connect_sqlite(path) as conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert tables >= EXPECTED_TABLES
        assert [row["version"] for row in versions] == [1, 2, 3, 4]
        assert conn.execute("pragma foreign_key_check").fetchall() == []


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

    with connect_sqlite(path) as conn:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        versions = conn.execute("select version from schema_migrations order by version").fetchall()
        assert "partial_table" not in tables
        assert [row["version"] for row in versions] == []
