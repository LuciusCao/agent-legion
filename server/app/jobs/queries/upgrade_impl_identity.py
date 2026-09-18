"""升级实现身份判定读取（issue #645 codex 四轮 P1-1，数据层 SQL）。

``agent_execution_requests.agent_definition_hash`` 是 dispatch 时刻的实现
身份（agent 行 = Agent 定义哈希、code 行 = code 文本 sha256）；
``job_workflow_upgrade_impl``（服务层判定）经本门面读取某 job 各节点
**最新完成请求**的身份记录，与当前 published 身份比较。SQL 在数据层，
服务层不手写查询（BOUNDARY-DATA-001）。
"""

from __future__ import annotations

from collections.abc import Collection

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class UpgradeImplIdentityQueriesMixin(ConnectionQueriesMixin):
    def latest_done_request_identities(
        self, job_id: str, node_keys: Collection[str]
    ) -> dict[str, tuple[str, str]]:
        """node_key → 该节点最新完成请求的 ``(kind, agent_definition_hash)``。

        取该节点最新一次 node_run 关联的 done 请求（completed 节点的产物
        由最后一次成功执行产出）；无关联请求（本地池执行 / retention 已
        清扫）→ 该节点不在返回值里，调用方按「实现身份不可证明」处理。
        """
        keys = sorted({str(key) for key in node_keys})
        if not keys:
            return {}
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select distinct on (r.node_key)
                       r.node_key, r.kind, r.agent_definition_hash
                from agent_execution_requests r
                join node_runs n on n.id = r.node_run_id
                where r.job_id = %s
                  and r.node_key = any(%s)
                  and r.state = 'done'
                  and n.status = 'completed'
                order by r.node_key, n.id desc
                """,
                (job_id, keys),
            ).fetchall()
        return {
            str(row["node_key"]): (str(row["kind"]), str(row["agent_definition_hash"] or ""))
            for row in rows
        }
