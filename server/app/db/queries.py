import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from server.app.db.notifications import NotificationHub
from server.app.db.schema import init_db
from server.app.pipeline.common import make_record_id
from server.app.pipeline.openclaw import extract_openclaw_arg
from server.app.records import PhaseRunRecord, VideoRecord
from server.app.services.interaction_stats import (
    compute_interaction_review_status,
    compute_interaction_stats,
)


def _iso(dt_str: str | None) -> str | None:
    """Convert SQLite timestamp (UTC) to ISO 8601 format with timezone."""
    if not dt_str:
        return None
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return dt.isoformat()


def _phase_run_with_agent_session(row: dict[str, Any]) -> PhaseRunRecord:
    try:
        command = json.loads(row.get("command_json") or "[]")
    except json.JSONDecodeError:
        command = []
    if isinstance(command, list):
        command_parts = [str(part) for part in command]
        row["agent_id"] = extract_openclaw_arg(command_parts, "--agent")
        row["agent_session_id"] = extract_openclaw_arg(command_parts, "--session-id")
    else:
        row["agent_id"] = ""
        row["agent_session_id"] = ""
    return cast(PhaseRunRecord, row)


VIDEO_UPDATE_FIELDS = {
    "source_url",
    "title",
    "content_type",
    "external_id",
    "knowledge_code",
    "question_id",
    "source_uuid",
    "storage_dir",
    "current_phase",
    "status",
    "duration",
    "error_message",
    "packed",
}


def _build_update_assignments(ordered_keys: list[str]) -> str:
    """Build SQL assignment clause from whitelisted keys.

    Keys are validated against VIDEO_UPDATE_FIELDS before calling this function.
    SQLite does not support parameterized column names, so we use string
    interpolation here. The caller must ensure keys come from the whitelist.
    """
    return ", ".join(f"{key}=?" for key in ordered_keys)


class VideoQueries:
    def __init__(
        self, path: Path, hub: NotificationHub | None = None, videos_dir: Path | None = None
    ):
        self.path = path
        self._hub = hub
        self._videos_dir = videos_dir
        self._read_conn: sqlite3.Connection | None = None
        self._read_conn_thread_id: int | None = None
        init_db(path)

    def _ensure_read_conn(self) -> sqlite3.Connection:
        current_tid = threading.current_thread().ident
        if self._read_conn is None or self._read_conn_thread_id != current_tid:
            self._read_conn = sqlite3.connect(self.path)
            self._read_conn.row_factory = sqlite3.Row
            self._read_conn_thread_id = current_tid
        return self._read_conn

    def close_read_conn(self) -> None:
        if self._read_conn is not None:
            self._read_conn.close()
            self._read_conn = None
            self._read_conn_thread_id = None

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self):
        """Read-only connection context that does not implicitly commit.

        If the current thread has already warmed up a persistent read
        connection via _ensure_read_conn(), it is reused. Otherwise a
        fresh connection is created and closed on exit.
        """
        current_tid = threading.current_thread().ident
        if self._read_conn is not None and self._read_conn_thread_id == current_tid:
            yield self._read_conn
            return
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _row(self, row: sqlite3.Row | None) -> VideoRecord | None:
        return cast(VideoRecord, dict(row)) if row else None

    def _notify(self, video_id: str) -> None:
        if self._hub is None:
            return
        video = self.get_video(video_id)
        if video is None:
            self._hub.emit_change(None)
            self._hub.emit_detail_change(video_id, cast(VideoRecord, {}), [], [])
            return
        if video.get("content_type") == "knowledge" and self._videos_dir is not None:
            video_dir = (
                Path(video["storage_dir"])
                if video.get("storage_dir")
                else self._videos_dir / video_id
            )
            stats = compute_interaction_stats(video_dir)
            if stats:
                video["interaction_stats"] = stats  # type: ignore[typeddict-unknown-key]
            video["interaction_review_status"] = compute_interaction_review_status(video_dir)  # type: ignore[typeddict-unknown-key]
        self._hub.emit_change(video)
        phase_runs = self.list_phase_runs(video_id)
        transcription_runs = self.list_transcription_runs(video_id)
        self._hub.emit_detail_change(video_id, video, phase_runs, transcription_runs)

    def create_video(
        self,
        source_url: str,
        title: str = "",
        storage_dir: str = "",
        content_type: str = "knowledge",
        external_id: str = "",
        source_uuid: str = "",
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
                  question_id, source_uuid, storage_dir, current_phase, status, packed
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                  source_url=excluded.source_url,
                  title=excluded.title,
                  content_type=excluded.content_type,
                  external_id=excluded.external_id,
                  knowledge_code=excluded.knowledge_code,
                  question_id=excluded.question_id,
                  source_uuid=excluded.source_uuid,
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
                    source_uuid,
                    storage_dir,
                    current_phase,
                    status,
                    0,
                ),
            )
            row = conn.execute("select * from videos where id=?", (video_id,)).fetchone()
        video = dict(row)
        self._notify(video_id)
        return cast(VideoRecord, video)

    def get_video(self, video_id: str) -> VideoRecord | None:
        with self._connect_read() as conn:
            return self._row(
                conn.execute("select * from videos where id=?", (video_id,)).fetchone()
            )

    def has_running_phase_run(self, video_id: str) -> bool:
        with self._connect_read() as conn:
            row = conn.execute(
                "select 1 from phase_runs where video_id=? and status='running' limit 1",
                (video_id,),
            ).fetchone()
            return row is not None

    def find_video_by_identity(self, content_type: str, external_id: str) -> VideoRecord | None:
        with self._connect_read() as conn:
            return self._row(
                conn.execute(
                    "select * from videos where content_type=? and external_id=?",
                    (content_type, external_id),
                ).fetchone()
            )

    def find_videos_by_identities(
        self, identities: list[tuple[str, str]]
    ) -> dict[tuple[str, str], VideoRecord]:
        if not identities:
            return {}
        conditions: list[str] = []
        params: list[Any] = []
        for content_type, external_id in identities:
            conditions.append("(content_type=? and external_id=?)")
            params.extend([content_type, external_id])
        where = " or ".join(conditions)
        with self._connect_read() as conn:
            rows = conn.execute(f"select * from videos where {where}", params).fetchall()
            return {
                (row["content_type"], row["external_id"]): cast(VideoRecord, dict(row))
                for row in rows
            }

    def list_videos(
        self,
        status_filter: str | list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[VideoRecord]:
        where_clauses: list[str] = []
        params: list[Any] = []

        if status_filter is not None:
            if isinstance(status_filter, str):
                where_clauses.append("status=?")
                params.append(status_filter)
            elif status_filter:
                placeholders = ",".join("?" * len(status_filter))
                where_clauses.append(f"status in ({placeholders})")
                params.extend(status_filter)

        sql = "select * from videos"
        if where_clauses:
            sql += " where " + " and ".join(where_clauses)
        sql += " order by created_at desc, id"
        if limit is not None:
            sql += " limit ? offset ?"
            params.extend([limit, offset])

        with self._connect_read() as conn:
            return [cast(VideoRecord, dict(row)) for row in conn.execute(sql, params)]

    def update_video(self, video_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown_fields = sorted(set(fields) - VIDEO_UPDATE_FIELDS)
        if unknown_fields:
            raise ValueError(f"Unknown video fields: {', '.join(unknown_fields)}")

        ordered_keys = [k for k in fields if k in VIDEO_UPDATE_FIELDS]
        assignments = _build_update_assignments(ordered_keys)
        values = [fields[key] for key in ordered_keys] + [video_id]
        sql = f"update videos set {assignments}, updated_at=current_timestamp where id=?"

        with self.connect() as conn:
            # Consistency check: status='completed' must pair with current_phase='assemble'
            if "status" in fields or "current_phase" in fields:
                row = conn.execute(
                    "select status, current_phase from videos where id=?", (video_id,)
                ).fetchone()
                if row:
                    new_status = fields.get("status", row["status"])
                    new_phase = fields.get("current_phase", row["current_phase"])
                    if new_status == "completed" and new_phase != "assemble":
                        raise ValueError(
                            f"Invalid state: status='completed' requires "
                            f"current_phase='assemble', got '{new_phase}'"
                        )
            conn.execute(sql, values)
        self._notify(video_id)

    def start_phase(
        self, video_id: str, phase_key: str, command: list[str], log_path: str = ""
    ) -> PhaseRunRecord | None:
        with self.connect() as conn:
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
        return _phase_run_with_agent_session(dict(row))

    def recover_running_videos(self) -> int:
        video_ids = []
        with self.connect() as conn:
            video_ids = [
                row["id"] for row in conn.execute("select id from videos where status='running'")
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

    def finish_phase(
        self, run_id: int, status: str, exit_code: int | None, error_message: str
    ) -> None:
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

    def update_phase_command(self, run_id: int, command: list[str]) -> None:
        video_id = None
        with self.connect() as conn:
            run = conn.execute("select video_id from phase_runs where id=?", (run_id,)).fetchone()
            conn.execute(
                "update phase_runs set command_json=? where id=?",
                (json.dumps(command), run_id),
            )
            if run:
                video_id = run["video_id"]
        if video_id:
            self._notify(video_id)

    def get_phase_run(self, video_id: str, run_id: int) -> PhaseRunRecord | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from phase_runs where video_id=? and id=?",
                (video_id, run_id),
            ).fetchone()
            if not row:
                return None
            phase_run = _phase_run_with_agent_session(dict(row))
            phase_run["started_at"] = _iso(phase_run["started_at"]) or ""
            phase_run["finished_at"] = _iso(phase_run["finished_at"])
            return phase_run

    def record_transcription_run(
        self,
        video_id: str,
        provider: str,
        status: str,
        srt_entry_count: int,
        validation_summary: str,
        fallback_reason: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into transcription_runs(
                  video_id,
                  provider,
                  status,
                  finished_at,
                  srt_entry_count,
                  validation_summary,
                  fallback_reason
                )
                values (?, ?, ?, current_timestamp, ?, ?, ?)
                """,
                (
                    video_id,
                    provider,
                    status,
                    srt_entry_count,
                    validation_summary,
                    fallback_reason,
                ),
            )
        self._notify(video_id)

    def list_phase_runs(self, video_id: str) -> list[PhaseRunRecord]:
        with self._connect_read() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "select * from phase_runs where video_id=? order by id", (video_id,)
                )
            ]
            for row in rows:
                row["started_at"] = _iso(row["started_at"]) or ""
                row["finished_at"] = _iso(row["finished_at"])
                _phase_run_with_agent_session(row)
            return cast(list[PhaseRunRecord], rows)

    def list_transcription_runs(self, video_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "select * from transcription_runs where video_id=? order by id", (video_id,)
                )
            ]
            for row in rows:
                row["started_at"] = _iso(row["started_at"]) or ""
                row["finished_at"] = _iso(row["finished_at"])
            return rows

    def clear_transcription_runs(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from transcription_runs where video_id=?", (video_id,))

    def delete_video(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from phase_runs where video_id=?", (video_id,))
            conn.execute("delete from transcription_runs where video_id=?", (video_id,))
            conn.execute("delete from videos where id=?", (video_id,))
        if self._hub is not None:
            self._hub.emit_delete(video_id)

    def batch_get_videos(self, video_ids: list[str]) -> list[VideoRecord]:
        if not video_ids:
            return []
        placeholders = ",".join("?" * len(video_ids))
        with self._connect_read() as conn:
            return [
                cast(VideoRecord, dict(row))
                for row in conn.execute(
                    f"select * from videos where id in ({placeholders})", video_ids
                )
            ]

    def batch_delete_videos(self, video_ids: list[str]) -> None:
        if not video_ids:
            return
        placeholders = ",".join("?" * len(video_ids))
        with self.connect() as conn:
            conn.execute(f"delete from phase_runs where video_id in ({placeholders})", video_ids)
            conn.execute(
                f"delete from transcription_runs where video_id in ({placeholders})",
                video_ids,
            )
            conn.execute(f"delete from videos where id in ({placeholders})", video_ids)
        if self._hub is not None:
            for vid in video_ids:
                self._hub.emit_delete(vid)
