from collections.abc import Callable
from typing import Any

from server.app.records import PhaseRunRecord, VideoRecord


class NotificationHub:
    """Holds callbacks and emits database change notifications."""

    def __init__(
        self,
        on_change: Callable[[VideoRecord], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_detail_change: Callable[
            [str, VideoRecord, list[PhaseRunRecord], list[dict[str, Any]]], None
        ]
        | None = None,
    ):
        self.on_change = on_change
        self.on_delete = on_delete
        self.on_detail_change = on_detail_change

    def emit_change(self, video: VideoRecord | None) -> None:
        if video is not None and self.on_change is not None:
            self.on_change(video)

    def emit_delete(self, video_id: str) -> None:
        if self.on_delete is not None:
            self.on_delete(video_id)

    def emit_detail_change(
        self,
        video_id: str,
        video: VideoRecord,
        phase_runs: list[PhaseRunRecord],
        transcription_runs: list[dict[str, Any]],
    ) -> None:
        if self.on_detail_change is not None:
            self.on_detail_change(video_id, video, phase_runs, transcription_runs)
