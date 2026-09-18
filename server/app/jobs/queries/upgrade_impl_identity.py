"""升级实现身份判定读取（issue #645 codex 四轮 P1-1，数据层 SQL）。

实现身份有两条同源记录，读取按 **node_runs 优先** 合并（#645 v85）：

- ``node_runs.agent_definition_hash``（schema v85）：claim 时刻写入的
  身份镜像（agent 行 = Agent 定义哈希、code 行 = code 文本 sha256）。
  node_runs 是 retention 永不删除的 audit trail，这层判定不受
  retention 窗口影响；本地 code 池执行（从不写请求行）的身份记录
  也落在这里。
- ``agent_execution_requests.agent_definition_hash``：dispatch 时刻的
  身份，请求行本身有独立消费者（claim 路由解析 join），对**段 1 未
  覆盖**的 node_key 作为 fallback——历史 Worker/Agent 作业（v85 前
  执行、请求行仍在 retention 窗内）由此保持零退化。

``job_workflow_upgrade_impl``（服务层判定）经本门面读取某 job 各节点
最新 completed 执行的身份记录，与当前 published 身份比较。SQL 在数据
层，服务层不手写查询（BOUNDARY-DATA-001）。
"""

from __future__ import annotations

from collections.abc import Collection

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class UpgradeImplIdentityQueriesMixin(ConnectionQueriesMixin):
    def latest_done_request_identities(
        self, job_id: str, node_keys: Collection[str]
    ) -> dict[str, tuple[str, str]]:
        """node_key → 该节点最新完成执行的 ``(kind, agent_definition_hash)``。

        段 1（node_runs 直查，v85+ 新执行）：每 node_key 取最新一条
        completed run 的身份列（``agent_definition_hash <> ''``——空串 =
        不可证明，不进段 1）。段 2（fallback）：现行请求行 SQL 原样，
        只对段 1 未覆盖的 node_key 执行——历史 Worker/Agent 作业在
        retention 窗内仍有 done 请求行。两段逐 node_key 二选一合并，
        node_runs 优先，不混行。无任何记录 → 该节点不在返回值里，
        调用方按「实现身份不可证明」处理。
        """
        keys = sorted({str(key) for key in node_keys})
        if not keys:
            return {}
        with self._connect_read() as conn:
            identities: dict[str, tuple[str, str]] = {
                # kind 空串哨兵（node_runs 段）：kind 是请求行的属性，
                # run 行没有——比较口径由服务层按 node_type 选（设计 §2.4），
                # 空串不等于任何真实 kind（'code'/'agent'），不会撞口径。
                str(row["node_key"]): ("", str(row["agent_definition_hash"]))
                for row in conn.execute(
                    """
                    select distinct on (node_key) node_key, agent_definition_hash
                    from node_runs
                    where job_id=%s
                      and node_key=any(%s)
                      and status='completed'
                      and agent_definition_hash<>''
                    order by node_key, id desc
                    """,
                    (job_id, keys),
                ).fetchall()
            }
            fallback_keys = [key for key in keys if key not in identities]
            if not fallback_keys:
                return identities
            request_rows = conn.execute(
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
                (job_id, fallback_keys),
            ).fetchall()
        for row in request_rows:
            key = str(row["node_key"])
            # 段 1 未覆盖（node_runs 无非空 hash 的 completed 行）才落请求
            # 行——两段不混：请求行是 enqueue 时刻身份、可能已被 retention
            # 清扫，node_runs 是 claim 时刻身份，混用会拿旧值覆盖新判定。
            if key in identities:
                continue
            identities[key] = (
                str(row["kind"]),
                str(row["agent_definition_hash"] or ""),
            )
        return identities
