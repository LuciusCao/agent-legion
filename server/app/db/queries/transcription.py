from __future__ import annotations

from server.app.db.queries.video import VideoQueriesMixin


class TranscriptionQueriesMixin(VideoQueriesMixin):
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

    def clear_transcription_runs(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from transcription_runs where video_id=?", (video_id,))
