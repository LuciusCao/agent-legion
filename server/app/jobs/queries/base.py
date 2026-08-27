from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.db.schema import init_db


class JobQueriesBase:
    def __init__(self, path: DatabaseDsn, jobs_dir: Path):
        # `path`（DSN）保留为实例属性，仅供数据层自身（queries/atomic_
        # mutations）与 executors lease 仓储使用——BOUNDARY-DATA-001 的
        # service 检查按 job_db.path 计数，service 侧取连接一律走
        # ConnectionQueriesMixin 的 connect/read/write 门面方法（#187：
        # 切断「任何拿到 JobQueries 的 service 都能自建连接」的 DSN 逃逸口）。
        self.path = path
        self.jobs_dir = jobs_dir
        init_db(path)
