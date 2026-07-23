from __future__ import annotations

from server.app.db.queries.base import VideoQueriesBase
from server.app.db.queries.package import PackageQueriesMixin


class VideoQueries(
    PackageQueriesMixin,
    VideoQueriesBase,
):
    """Concrete database query class composing the remaining query mixins."""
