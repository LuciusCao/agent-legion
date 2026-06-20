from __future__ import annotations

import sqlite3

from server.app.db.queries.base import VideoQueriesBase
from server.app.db.queries.video import VideoQueriesMixin
from server.app.db.queries.phase_run import PhaseRunQueriesMixin
from server.app.db.queries.transcription import TranscriptionQueriesMixin
from server.app.db.queries.package import PackageQueriesMixin


class VideoQueries(
    VideoQueriesMixin,
    PhaseRunQueriesMixin,
    TranscriptionQueriesMixin,
    PackageQueriesMixin,
    VideoQueriesBase,
):
    """Backward-compatible facade for all video/query operations."""
