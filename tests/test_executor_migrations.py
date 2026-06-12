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


def test_v004_migration_preserves_existing_data(tmp_path: Path) -> None:
    """A pre-V004 database with data survives the rebuild with no column misalignment."""
    path = tmp_path / "v004_preserve.sqlite"
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
            insert into workspaces(id, name) values ('ws1', 'Legacy Workspace');
            insert into job_batches(
              id, workspace_id, pipeline_key, source_kind, source_payload_json,
              status, created_count, error_message, created_at
            ) values (
              'batch1', 'ws1', 'reading_analysis', 'question', '{}',
              'created', 5, '', '2024-01-01 09:00:00'
            );
            insert into jobs(
              id, workspace_id, pipeline_key, source_type, source_id, batch_id,
              title, status, storage_dir, error_message, created_at, updated_at, stem
            ) values (
              'job1', 'ws1', 'reading_analysis', 'question', 'q1', 'batch1',
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
    conn.close()

    init_db(path)

    with connect_sqlite(path) as conn:
        batch = conn.execute("select * from job_batches where id = 'batch1'").fetchone()
        assert batch is not None
        assert batch["workspace_id"] == "ws1"
        assert batch["pipeline_key"] == "reading_analysis"
        assert batch["source_kind"] == "question"
        assert batch["created_count"] == 5
        assert batch["created_at"] == "2024-01-01 09:00:00"

        job = conn.execute("select * from jobs where id = 'job1'").fetchone()
        assert job is not None
        assert job["workspace_id"] == "ws1"
        assert job["pipeline_key"] == "reading_analysis"
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

    with connect_sqlite(path) as conn:
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

    with connect_sqlite(path) as conn:
        conn.execute("insert into workspaces(id, name) values ('ws1', 'Workspace One')")
        conn.execute(
            "insert into job_batches(id, workspace_id, pipeline_key, source_kind) "
            "values ('batch1', 'ws1', 'question_content', 'mixed')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, pipeline_key, source_type, source_id) "
            "values ('job1', 'ws1', 'question_content', 'question_id', 'Q1')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job1', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status) values ('job1', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, "
            "pipeline_key, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) "
            "values ('lease1', 'exec1', 'exec_a', 'ws1', 'job1', 'question_content', "
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
