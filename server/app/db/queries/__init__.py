from __future__ import annotations

from server.app.db.queries.base import VideoQueriesBase
from server.app.db.queries.package import PackageQueriesMixin
from server.app.db.queries.phase_run import PhaseRunQueriesMixin
from server.app.db.queries.transcription import TranscriptionQueriesMixin


class VideoQueries(
    PhaseRunQueriesMixin,
    TranscriptionQueriesMixin,
    PackageQueriesMixin,
    VideoQueriesBase,
):
    """Backward-compatible facade for all video/query operations."""
