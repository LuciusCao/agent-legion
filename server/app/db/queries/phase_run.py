from __future__ import annotations

import json
from typing import cast

from server.app.db.queries.video import VideoQueriesMixin, _iso, _phase_run_with_agent_session
from server.app.records import PhaseRunRecord


class PhaseRunQueriesMixin(VideoQueriesMixin):
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
