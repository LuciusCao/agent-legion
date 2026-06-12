import sqlite3
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import MIGRATIONS, run_migrations


def _execute_statements(conn: sqlite3.Connection, sql: str) -> None:
    """Split and execute a multi-statement SQL script within the current transaction."""
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def init_db(path: Path) -> None:
    """Create tables and run lightweight migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(path)
    try:
        with conn:
            _execute_statements(
                conn,
                """
                create table if not exists videos (
                  id text primary key,
                  source_url text not null,
                  title text not null,
                  content_type text not null default 'knowledge',
                  external_id text not null default '',
                  knowledge_code text not null default '',
                  question_id text not null default '',
                  source_uuid text not null default '',
                  storage_dir text not null default '',
                  current_phase text not null default 'download',
                  status text not null default 'queued',
                  duration real not null default 0,
                  error_message text not null default '',
                  created_at text not null default current_timestamp,
                  updated_at text not null default current_timestamp
                );
                create table if not exists phase_runs (
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
                create table if not exists transcription_runs (
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
                create table if not exists packages (
                  id integer primary key autoincrement,
                  path text not null,
                  created_at text not null default current_timestamp
                );
                create table if not exists workspaces (
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
                create table if not exists job_batches (
                  id text primary key,
                  workspace_id text not null default 'default',
                  pipeline_key text not null,
                  source_kind text not null,
                  source_payload_json text not null default '{}',
                  status text not null default 'created',
                  created_count integer not null default 0,
                  error_message text not null default '',
                  created_at text not null default current_timestamp,
                  foreign key(workspace_id) references workspaces(id) on delete cascade
                );
                create table if not exists jobs (
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
                  foreign key(workspace_id) references workspaces(id) on delete cascade
                );
                create table if not exists job_nodes (
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
                create table if not exists node_runs (
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
                  session_dir text not null default '',
                  foreign key(job_id) references jobs(id) on delete cascade
                );
                create table if not exists workspace_agent_assignments (
                  workspace_id text not null,
                  agent_id text not null,
                  concurrency_limit integer not null default 1,
                  primary key (workspace_id, agent_id)
                );
                """,
            )
            conn.execute(
                """
                insert into workspaces(id, name, default_pipeline_key)
                values ('default', '默认工作空间', 'reading_analysis')
                on conflict(id) do nothing
                """
            )
            conn.execute(
                """
                update workspaces
                set default_pipeline_key = 'reading_analysis'
                where id = 'default' and default_pipeline_key = 'question_content'
                """
            )

        run_migrations(conn, MIGRATIONS)

        # Performance indexes for issue 012. These are created after migrations
        # so that columns added by V003 (e.g. videos.content_type) are present.
        _execute_statements(
            conn,
            """
            create index if not exists idx_videos_status on videos(status);
            create index if not exists idx_videos_content_type_external_id on videos(content_type, external_id);
            create index if not exists idx_videos_created_at on videos(created_at);
            create index if not exists idx_phase_runs_video_id on phase_runs(video_id);
            create index if not exists idx_phase_runs_video_id_status on phase_runs(video_id, status);
            create index if not exists idx_transcription_runs_video_id on transcription_runs(video_id);
            create index if not exists idx_jobs_pipeline_status on jobs(pipeline_key, status);
            create index if not exists idx_jobs_source on jobs(pipeline_key, source_type, source_id);
            create index if not exists idx_workspaces_created_at on workspaces(created_at);
            create index if not exists idx_job_batches_workspace on job_batches(workspace_id, created_at);
            create index if not exists idx_jobs_workspace_pipeline_status on jobs(workspace_id, pipeline_key, status);
            create index if not exists idx_jobs_workspace_source on jobs(workspace_id, pipeline_key, source_type, source_id);
            create index if not exists idx_job_nodes_job_status on job_nodes(job_id, status);
            create index if not exists idx_node_runs_job_id on node_runs(job_id);
            create index if not exists idx_workspace_agent_assignments on workspace_agent_assignments(workspace_id, agent_id);
            """,
        )
    finally:
        conn.close()
