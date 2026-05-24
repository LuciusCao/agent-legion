import json
import sqlite3
from pathlib import Path
from typing import Any

from server.app.pipeline.common import make_record_id
from server.app.records import PhaseRunRecord, VideoRecord

VIDEO_UPDATE_FIELDS = {
    "source_url",
    "title",
    "content_type",
    "external_id",
    "knowledge_code",
    "question_id",
    "storage_dir",
    "current_phase",
    "status",
    "duration",
    "error_message",
}


class Database:
    def __init__(self, path: Path, on_change=None, on_delete=None):
        self.path = path
        self._on_change = on_change
        self._on_delete = on_delete
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
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
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    conn.execute(statement)

    def _row(self, row: sqlite3.Row | None) -> VideoRecord | None:
        return dict(row) if row else None

    def _notify(self, video_id: str) -> None:
        if self._on_change is None:
            return
        video = self.get_video(video_id)
        if video:
            self._on_change(video)

    def create_video(
        self,
        source_url: str,
        title: str = "",
        storage_dir: str = "",
        content_type: str = "knowledge",
        external_id: str = "",
    ) -> VideoRecord:
        content_type = content_type if content_type in {"knowledge", "question"} else "knowledge"
        video_id = make_record_id(source_url, content_type, external_id)
        status = "queued" if source_url else "missing_url"
        current_phase = "download" if source_url else "waiting_for_url"
        knowledge_code = external_id if content_type == "knowledge" else ""
        question_id = external_id if content_type == "question" else ""
        with self.connect() as conn:
            conn.execute(
                """
                insert into videos(
                  id, source_url, title, content_type, external_id, knowledge_code,
                  question_id, storage_dir, current_phase, status
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                  source_url=excluded.source_url,
                  title=excluded.title,
                  content_type=excluded.content_type,
                  external_id=excluded.external_id,
                  knowledge_code=excluded.knowledge_code,
                  question_id=excluded.question_id,
                  current_phase=excluded.current_phase,
                  status=excluded.status,
                  updated_at=current_timestamp
                """,
                (
                    video_id,
                    source_url,
                    title or external_id or video_id,
                    content_type,
                    external_id,
                    knowledge_code,
                    question_id,
                    storage_dir,
                    current_phase,
                    status,
                ),
            )
            row = conn.execute("select * from videos where id=?", (video_id,)).fetchone()
        video = dict(row)
        self._notify(video_id)
        return video

    def get_video(self, video_id: str) -> VideoRecord | None:
        with self.connect() as conn:
            return self._row(conn.execute("select * from videos where id=?", (video_id,)).fetchone())

    def find_video_by_identity(self, content_type: str, external_id: str) -> VideoRecord | None:
        with self.connect() as conn:
            return self._row(
                conn.execute(
                    "select * from videos where content_type=? and external_id=?",
                    (content_type, external_id),
                ).fetchone()
            )

    def list_videos(self) -> list[VideoRecord]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("select * from videos order by created_at desc, id")]

    def update_video(self, video_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown_fields = sorted(set(fields) - VIDEO_UPDATE_FIELDS)
        if unknown_fields:
            raise ValueError(f"Unknown video fields: {', '.join(unknown_fields)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [video_id]
        with self.connect() as conn:
            conn.execute(
                f"update videos set {assignments}, updated_at=current_timestamp where id=?",
                values,
            )
        self._notify(video_id)

    def start_phase(
        self, video_id: str, phase_key: str, command: list[str], log_path: str = ""
    ) -> PhaseRunRecord | None:
        with self.connect() as conn:
            # Atomic claim: only videos ready to be claimed can transition to running.
            updated = conn.execute(
                "update videos set current_phase=?, status='running', updated_at=current_timestamp where id=? and status in ('queued', 'missing_url')",
                (phase_key, video_id),
            ).rowcount
            if updated == 0:
                return None
            cur = conn.execute(
                """
                insert into phase_runs(video_id, phase_key, status, command_json, log_path)
                values (?, ?, 'running', ?, ?)
                """,
                (video_id, phase_key, json.dumps(command), log_path),
            )
            row = conn.execute("select * from phase_runs where id=?", (cur.lastrowid,)).fetchone()
        self._notify(video_id)
        return dict(row)

    def recover_running_videos(self) -> int:
        video_ids = []
        with self.connect() as conn:
            video_ids = [
                row["id"]
                for row in conn.execute("select id from videos where status='running'")
            ]
            conn.execute(
                """
                update phase_runs
                set status='failed',
                    exit_code=-1,
                    error_message='worker interrupted before restart',
                    finished_at=current_timestamp
                where status='running'
                """
            )
            conn.execute(
                """
                update videos
                set status='queued', error_message='', updated_at=current_timestamp
                where status='running'
                """
            )
        for vid in video_ids:
            self._notify(vid)
        return len(video_ids)

    def finish_phase(self, run_id: int, status: str, exit_code: int | None, error_message: str) -> None:
        video_id = None
        with self.connect() as conn:
            run = conn.execute("select * from phase_runs where id=?", (run_id,)).fetchone()
            conn.execute(
                """
                update phase_runs
                set status=?, exit_code=?, error_message=?, finished_at=current_timestamp
                where id=?
                """,
                (status, exit_code, error_message, run_id),
            )
            if run:
                video_id = run["video_id"]
                conn.execute(
                    """
                    update videos
                    set status=?, error_message=?, updated_at=current_timestamp
                    where id=?
                    """,
                    (status, error_message, video_id),
                )
        if video_id:
            self._notify(video_id)

    def list_phase_runs(self, video_id: str) -> list[PhaseRunRecord]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "select * from phase_runs where video_id=? order by id", (video_id,)
                )
            ]

    def delete_video(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from phase_runs where video_id=?", (video_id,))
            conn.execute("delete from transcription_runs where video_id=?", (video_id,))
            conn.execute("delete from videos where id=?", (video_id,))
        if self._on_delete is not None:
            self._on_delete(video_id)
