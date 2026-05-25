import sqlite3
from pathlib import Path


def init_db(path: Path) -> None:
    """Create tables and run lightweight migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
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
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                conn.execute(statement)
