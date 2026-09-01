from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobArtifactKeyQueriesMixin(ConnectionQueriesMixin):
    def all_artifact_storage_keys(self) -> set[str]:
        """Return every ``storage_key`` present in ``job_artifacts``.

        S3 jobs GC（#340）的孤儿判定用：列举出的对象 key 不在此集合中
        且超宽限窗即孤儿。``storage_key`` 无索引，按 key 反查会退化为
        每批一次全表扫描，故一次性单遍全量载入（运维 CLI 的有意取舍；
        运行时路径不得使用本方法）。
        """
        with self._connect_read() as conn:
            rows = conn.execute("select storage_key from job_artifacts")
            return {str(row["storage_key"]) for row in rows}
