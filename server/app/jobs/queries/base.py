from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.db.schema import init_db


class JobQueriesBase:
    def __init__(self, path: DatabaseDsn, jobs_dir: Path):
        # `_path`（DSN）是数据层私有属性，仅供 queries/atomic_mutations
        # 内部使用——#187 第三步：`.path` 已私有化，`dsn_identity` 是唯一
        # 公开只读访问器（BOUNDARY-DATA-001；数据层之外的连接获取一律走
        # ConnectionQueriesMixin 的 connect/read/write 门面方法，lease 仓储
        # 与 artifact store 经 dsn_identity 持有字符串 DSN）。直接读
        # `job_db._path` 属于数据层自我约定，service 侧出现即违规。
        self._path = path
        self.jobs_dir = jobs_dir
        init_db(path)
