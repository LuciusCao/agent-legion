from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from server.app.db.notifications import NotificationHub
from server.app.db.queries.base import VideoQueriesBase
from server.app.pipeline.common import make_record_id, resolve_video_dir
from server.app.pipeline.openclaw import extract_openclaw_arg
from server.app.records import PhaseRunRecord, VideoRecord
from server.app.services.interaction_stats import (
    _backfill_interaction_stats,
    _enrich_video,
)


def _iso(dt_str: str | None) -> str | None:
    """Convert SQLite timestamp (UTC) to ISO 8601 format with timezone."""
    if not dt_str:
        return None
    from datetime import UTC, datetime

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
    "interaction_stats_json",
    "interaction_review_status",
}


def _build_update_assignments(ordered_keys: list[str]) -> str:
    """Build SQL assignment clause from whitelisted keys.

    Keys are validated against VIDEO_UPDATE_FIELDS before calling this function.
    SQLite does not support parameterized column names, so we use string
    interpolation here. The caller must ensure keys come from the whitelist.
    """
    return ", ".join(f"{key}=?" for key in ordered_keys)


class VideoQueriesMixin(VideoQueriesBase):
    _hub: NotificationHub | None
    _videos_dir: Path | None

    def _list_phase_runs_with_conn(
        self, conn: sqlite3.Connection, video_id: str
    ) -> list[PhaseRunRecord]:
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

    def _list_transcription_runs_with_conn(
        self, conn: sqlite3.Connection, video_id: str
    ) -> list[dict[str, Any]]:
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

    def _notify_with_conn(self, video_id: str, conn: sqlite3.Connection) -> None:
        if self._hub is None:
            return
        row = conn.execute("select * from videos where id=?", (video_id,)).fetchone()
        video = self._row(row)
        if video is None:
            self._hub.emit_change(None)
            self._hub.emit_detail_change(video_id, cast(VideoRecord, {}), [], [])
            return
        _enrich_video(video)
        if (
            video.get("content_type") == "knowledge"
            and "interaction_stats" not in video
            and self._videos_dir is not None
        ):
            video_dir = resolve_video_dir(video, self._videos_dir)
            _backfill_interaction_stats(video, video_dir)
        self._hub.emit_change(video)
        phase_runs = self._list_phase_runs_with_conn(conn, video_id)
        transcription_runs = self._list_transcription_runs_with_conn(conn, video_id)
        self._hub.emit_detail_change(video_id, video, phase_runs, transcription_runs)

    def _notify(self, video_id: str) -> None:
        with self._connect_read() as conn:
            self._notify_with_conn(video_id, conn)

    def batch_notify(self, video_ids: list[str]) -> None:
        if self._hub is None or not video_ids:
            return
        with self._connect_read() as conn:
            for vid in video_ids:
                try:
                    self._notify_with_conn(vid, conn)
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception("batch_notify failed for %s", vid)

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

    def list_running_video_summaries(self) -> list[dict[str, Any]]:
        """Return minimal fields for running videos (used by recovery)."""
        with self._connect_read() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "select id, current_phase, storage_dir from videos where status='running'"
                )
            ]

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

    def batch_update_packed(
        self, video_ids: list[str], packed: int = 1, *, notify: bool = True
    ) -> None:
        if not video_ids:
            return
        placeholders = ",".join("?" * len(video_ids))
        sql = (
            f"update videos set packed=?, updated_at=current_timestamp where id in ({placeholders})"
        )
        with self.connect() as conn:
            conn.execute(sql, [packed] + video_ids)
        if notify:
            self.batch_notify(video_ids)

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
