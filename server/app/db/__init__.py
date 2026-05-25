from server.app.db.queries import VideoQueries


class Database(VideoQueries):
    """Backward-compatible facade for video database operations."""

    pass

__all__ = ["Database"]
