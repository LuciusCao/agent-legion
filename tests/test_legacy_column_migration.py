import sqlite3
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS, run_migrations
from server.app.db.migrations.v003_legacy_columns import MIGRATION as V003
from server.app.db.schema import init_db

# Legacy schemas simulate a database created before V003. They omit every
# column group that V003 is responsible for adding.
_LEGACY_SCHEMA_SQL = """
create table videos (
  id text primary key,
  source_url text not null,
  title text not null,
  storage_dir text not null default '',
  current_phase text not null default 'download',
  status text not null default 'queued',
  duration real not null default 0,
  error_message text not null default '',
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp
);
create table phase_runs (
  id integer primary key autoincrement,
  video_id text not null,
  phase_key text not null,
  status text not null,
  started_at text not null default current_timestamp,
  finished_at text,
  command_json text not null default '[]',
  exit_code integer,
  log_path text not null default '',
  error_message text not null default '',
  foreign key(video_id) references videos(id) on delete cascade
);
create table transcription_runs (
  id integer primary key autoincrement,
  video_id text not null,
  provider text not null,
  status text not null,
  started_at text not null default current_timestamp,
  finished_at text,
  srt_entry_count integer not null default 0,
  validation_summary text not null default '',
  fallback_reason text not null default '',
  foreign key(video_id) references videos(id) on delete cascade
);
create table packages (
  id integer primary key autoincrement,
  path text not null,
  created_at text not null default current_timestamp
);
create table workspaces (
  id text primary key,
  name text not null,
  default_pipeline_key text not null default 'question_content',
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp
);
create table job_batches (
  id text primary key,
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
  unique(job_id, node_key),
  foreign key(job_id) references jobs(id) on delete cascade
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
  foreign key(job_id) references jobs(id) on delete cascade
);
"""

_LEGACY_TABLES = (
    "videos",
    "phase_runs",
    "transcription_runs",
    "packages",
    "workspaces",
    "job_batches",
    "jobs",
    "job_nodes",
    "node_runs",
)

_V003_ADDED_COLUMNS: dict[str, set[str]] = {
    "videos": {
        "content_type",
        "external_id",
        "knowledge_code",
        "question_id",
        "source_uuid",
        "packed",
        "interaction_stats_json",
        "interaction_review_status",
    },
    "workspaces": {
        "cms_config_json",
        "resource_config_json",
        "default_entity",
        "intake_config_json",
        "description",
        "pipeline_config_json",
    },
    "packages": {"video_count", "size_bytes", "name", "locked"},
    "job_batches": {"workspace_id"},
    "jobs": {"workspace_id", "stem"},
    "node_runs": {"run_dir", "session_dir"},
}


def _create_legacy_fixture(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(path)
    conn.executescript(_LEGACY_SCHEMA_SQL)
    with conn:
        conn.execute("insert into workspaces(id, name) values ('default', 'Default Workspace')")
        conn.execute(
            "insert into videos(id, source_url, title) values ('v1', 'https://example.com/v1.mp4', 'Video 1')"
        )
        conn.execute("insert into packages(path) values ('/tmp/p1.zip')")
        conn.execute(
            "insert into job_batches(id, pipeline_key, source_kind) values ('b1', 'question_content', 'question')"
        )
        conn.execute(
            "insert into jobs(id, pipeline_key, source_type, source_id) values ('j1', 'question_content', 'question', 'Q001')"
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('j1', 'extract')")
        conn.execute(
            "insert into node_runs(job_id, node_key, status) values ('j1', 'extract', 'completed')"
        )
    return conn


def _column_info(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, object]]:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return {
        row["name"]: {
            "name": row["name"],
            "type": row["type"],
            "notnull": row["notnull"],
            "dflt_value": row["dflt_value"],
            "pk": row["pk"],
        }
        for row in rows
    }


def test_v003_adds_missing_columns_and_preserves_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    conn = _create_legacy_fixture(path)
    conn.close()

    conn = connect_sqlite(path)
    run_migrations(conn, (V003,))
    conn.close()

    conn = connect_sqlite(path)
    for table, expected_columns in _V003_ADDED_COLUMNS.items():
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
        assert expected_columns.issubset(columns), (
            f"{table} missing columns: {expected_columns - columns}"
        )

    video = conn.execute("select * from videos where id = 'v1'").fetchone()
    assert video["content_type"] == "knowledge"
    assert video["external_id"] == ""
    assert video["knowledge_code"] == ""
    assert video["question_id"] == ""
    assert video["source_uuid"] == ""
    assert video["packed"] == 0
    assert video["interaction_stats_json"] == ""
    assert video["interaction_review_status"] == ""

    package = conn.execute("select * from packages").fetchone()
    assert package["video_count"] == 0
    assert package["size_bytes"] == 0
    assert package["name"] == ""
    assert package["locked"] == 0

    workspace = conn.execute("select * from workspaces where id = 'default'").fetchone()
    assert workspace["cms_config_json"] == "{}"
    assert workspace["resource_config_json"] == "{}"
    assert workspace["default_entity"] == "question"
    assert workspace["intake_config_json"] == "{}"
    assert workspace["description"] == ""
    assert workspace["pipeline_config_json"] == "{}"

    batch = conn.execute("select * from job_batches").fetchone()
    assert batch["workspace_id"] == "default"

    job = conn.execute("select * from jobs").fetchone()
    assert job["workspace_id"] == "default"
    assert job["stem"] == ""

    node_run = conn.execute("select * from node_runs").fetchone()
    assert node_run["run_dir"] == ""
    assert node_run["session_dir"] == ""

    # Values written before the migration must survive.
    assert video["title"] == "Video 1"
    assert package["path"] == "/tmp/p1.zip"
    assert batch["pipeline_key"] == "question_content"
    assert job["source_id"] == "Q001"
    assert node_run["status"] == "completed"
    conn.close()


def test_v003_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    conn = _create_legacy_fixture(path)
    conn.close()

    conn = connect_sqlite(path)
    run_migrations(conn, (V003,))
    run_migrations(conn, (V003,))
    conn.close()

    conn = connect_sqlite(path)
    for table in _V003_ADDED_COLUMNS:
        rows = conn.execute(f"select name from pragma_table_info('{table}')").fetchall()
        names = {row["name"] for row in rows}
        assert _V003_ADDED_COLUMNS[table].issubset(names)
    conn.close()


def test_fresh_and_legacy_db_schemas_are_equivalent(tmp_path: Path) -> None:
    fresh_path = tmp_path / "fresh.sqlite"
    init_db(fresh_path)

    legacy_path = tmp_path / "legacy.sqlite"
    conn = _create_legacy_fixture(legacy_path)
    conn.close()
    init_db(legacy_path)

    fresh_conn = connect_sqlite(fresh_path)
    legacy_conn = connect_sqlite(legacy_path)
    try:
        for table in _LEGACY_TABLES:
            fresh_info = _column_info(fresh_conn, table)
            legacy_info = _column_info(legacy_conn, table)
            assert set(fresh_info) == set(legacy_info), (
                f"{table} column names differ: {set(fresh_info) ^ set(legacy_info)}"
            )
            for name in fresh_info:
                assert fresh_info[name] == legacy_info[name], (
                    f"{table}.{name} schema differs: fresh={fresh_info[name]} legacy={legacy_info[name]}"
                )
    finally:
        fresh_conn.close()
        legacy_conn.close()


def test_init_db_records_all_migrations(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite"
    init_db(path)

    conn = connect_sqlite(path)
    versions = {
        row["version"] for row in conn.execute("select version from schema_migrations").fetchall()
    }
    # V005 is applied by the one-time legacy finalizer, not by init_db.
    expected = {m.version for m in MIGRATIONS if m.version < 5}
    assert versions == expected, f"Missing migrations: {expected - versions}"
    conn.close()
