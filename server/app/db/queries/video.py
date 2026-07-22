from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from server.app.db.connection import DatabaseConnection
from server.app.db.notifications import NotificationHub
from server.app.db.queries.base import VideoQueriesBase
from server.app.pipeline.openclaw import extract_openclaw_arg
from server.app.records import PhaseRunRecord, VideoRecord


def _iso(value: datetime | str | None) -> str | None:
    """Return a PostgreSQL timestamp as an ISO 8601 string."""
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
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


class VideoQueriesMixin(VideoQueriesBase):
    _hub: NotificationHub | None
    _videos_dir: Path | None

    def _list_phase_runs_with_conn(
        self, conn: DatabaseConnection, video_id: str
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
        self, conn: DatabaseConnection, video_id: str
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

    def _notify_with_conn(self, video_id: str, conn: DatabaseConnection) -> None:
        if self._hub is None:
            return
        row = conn.execute("select * from videos where id=?", (video_id,)).fetchone()
        video = self._row(row)
        if video is None:
            self._hub.emit_change(None)
            self._hub.emit_detail_change(video_id, cast(VideoRecord, {}), [], [])
            return
        self._hub.emit_change(video)
        phase_runs = self._list_phase_runs_with_conn(conn, video_id)
        transcription_runs = self._list_transcription_runs_with_conn(conn, video_id)
        self._hub.emit_detail_change(video_id, video, phase_runs, transcription_runs)

    def _notify(self, video_id: str) -> None:
        with self._connect_read() as conn:
            self._notify_with_conn(video_id, conn)
