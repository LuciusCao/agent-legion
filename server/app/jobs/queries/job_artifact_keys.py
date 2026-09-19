from __future__ import annotations

from collections.abc import Collection

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobArtifactKeyQueriesMixin(ConnectionQueriesMixin):
    def job_artifact_manifest_names_for_nodes(
        self, job_id: str, node_keys: Collection[str]
    ) -> set[tuple[str, str]]:
        """(node_key, name) 清单行集合，限定给定节点（inherit 升级预检）。

        继承模式判断一个拟继承节点的产物是否仍可从对象存储回填：有清单
        行 = 执行前 restore_missing_inputs 可达（EXEC-ARTIFACT-STORE-001）。
        返回空集合时调用方按「全部本地缺失 → 退化重跑」处理。
        """
        if not node_keys:
            return set()
        placeholders = ",".join("%s" for _ in node_keys)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select node_key, name from job_artifacts"
                f" where job_id=%s and node_key in ({placeholders})",
                (job_id, *node_keys),
            ).fetchall()
        return {(str(row["node_key"]), str(row["name"])) for row in rows}

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

    def all_object_storage_keys(self) -> set[str]:
        """Return the union of ``storage_key`` across ``job_artifacts`` and
        ``materials``.

        GC 判定的完整引用集合（系统性评审 P1，#344）：materials 的 key
        形如 ``{workspace_id}/{content_hash}/{filename}``，workspace id
        允许叫 ``jobs`` / ``jobs-staging``（无保留名约束）——那样的材料
        key 会落进 GC 的两个前缀，只对照 job_artifacts 会把它们全部
        误判为孤儿删掉。运维 CLI 专用，运行时路径不得使用。
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select storage_key from job_artifacts union select storage_key from materials"
            )
            return {str(row["storage_key"]) for row in rows}

    def existing_object_storage_keys(self, storage_keys: list[str]) -> set[str]:
        """Return the subset of ``storage_keys`` currently present in
        ``job_artifacts`` or ``materials``——GC 删除前的新鲜反查（无索引
        列上每批一次顺序扫描，apply 路径的正确性代价，扫描/判定路径
        不用它）。

        TOCTOU 防护（#344 评审 P1）：扫描与删除之间并发 promote 可能
        重建同名 key 并建行；删除批按本反查过滤掉「此刻已有行」的 key，
        避免误删新权威对象。对照 materials 是 P1 修复的一部分：workspace
        名撞上前缀的材料 key 也必须被删除前的反查救下。
        """
        if not storage_keys:
            return set()
        with self._connect_read() as conn:
            rows = conn.execute(
                "select storage_key from job_artifacts where storage_key = ANY(%s)"
                " union"
                " select storage_key from materials where storage_key = ANY(%s)",
                (storage_keys, storage_keys),
            )
            return {str(row["storage_key"]) for row in rows}
