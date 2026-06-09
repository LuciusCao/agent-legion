import sqlite3
from pathlib import Path


def init_db(path: Path) -> None:
    """Create tables and run lightweight migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.executescript(
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
                  error_message text not null default ''
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
                  fallback_reason text not null default ''
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
                  updated_at text not null default current_timestamp
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
                  created_at text not null default current_timestamp
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
                  updated_at text not null default current_timestamp
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
                  unique(job_id, node_key)
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
                  error_message text not null default ''
                );
                create table if not exists workspace_agent_assignments (
                  workspace_id text not null,
                  agent_id text not null,
                  concurrency_limit integer not null default 1,
                  primary key (workspace_id, agent_id)
                );
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("pragma table_info(videos)").fetchall()
            }
            migrations = {
                "content_type": "alter table videos add column content_type text not null default 'knowledge'",
                "external_id": "alter table videos add column external_id text not null default ''",
                "knowledge_code": "alter table videos add column knowledge_code text not null default ''",
                "question_id": "alter table videos add column question_id text not null default ''",
                "source_uuid": "alter table videos add column source_uuid text not null default ''",
                "packed": "alter table videos add column packed integer not null default 0",
                "interaction_stats_json": "alter table videos add column interaction_stats_json text not null default ''",
                "interaction_review_status": "alter table videos add column interaction_review_status text not null default ''",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    conn.execute(statement)

            existing_workspace_columns = {
                row["name"] for row in conn.execute("pragma table_info(workspaces)").fetchall()
            }
            workspace_migrations = {
                "cms_config_json": (
                    "alter table workspaces add column cms_config_json text not null default '{}'"
                ),
                "resource_config_json": (
                    "alter table workspaces add column resource_config_json text not null default '{}'"
                ),
                "default_entity": (
                    "alter table workspaces add column default_entity text not null default 'question'"
                ),
                "intake_config_json": (
                    "alter table workspaces add column intake_config_json text not null default '{}'"
                ),
                "description": (
                    "alter table workspaces add column description text not null default ''"
                ),
            }
            for column, statement in workspace_migrations.items():
                if column not in existing_workspace_columns:
                    conn.execute(statement)

            existing_package_columns = {
                row["name"] for row in conn.execute("pragma table_info(packages)").fetchall()
            }
            package_migrations = {
                "video_count": "alter table packages add column video_count integer not null default 0",
                "size_bytes": "alter table packages add column size_bytes integer not null default 0",
                "name": "alter table packages add column name text not null default ''",
                "locked": "alter table packages add column locked integer not null default 0",
            }
            for column, statement in package_migrations.items():
                if column not in existing_package_columns:
                    conn.execute(statement)

            conn.execute(
                """
                insert into workspaces(id, name, default_pipeline_key)
                values ('default', '默认工作空间', 'question_content')
                on conflict(id) do nothing
                """
            )

            existing_job_batch_columns = {
                row["name"] for row in conn.execute("pragma table_info(job_batches)").fetchall()
            }
            job_batch_migrations = {
                "workspace_id": (
                    "alter table job_batches add column workspace_id text not null default 'default'"
                ),
            }
            for column, statement in job_batch_migrations.items():
                if column not in existing_job_batch_columns:
                    conn.execute(statement)

            existing_job_columns = {
                row["name"] for row in conn.execute("pragma table_info(jobs)").fetchall()
            }
            job_migrations = {
                "workspace_id": "alter table jobs add column workspace_id text not null default 'default'",
            }
            for column, statement in job_migrations.items():
                if column not in existing_job_columns:
                    conn.execute(statement)

            # Performance indexes for issue 012
            conn.executescript(
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
                """
            )
    finally:
        conn.close()
