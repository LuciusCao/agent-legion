from server.app.db.notifications import NotificationHub
from server.app.db.queries import VideoQueries
from server.app.db.schema import init_db

Database = VideoQueries

__all__ = ["Database", "NotificationHub", "VideoQueries", "init_db"]
