"""升级实现身份判定读取（issue #645 codex 四轮 P1-1，数据层 SQL）。

实现身份有两条同源记录，读取先固定每个节点的**最新 completed run**，
再在同一次 run 内按 **node_runs 优先** 合并（#645 v85）：

- ``node_runs.agent_definition_hash``（schema v85）：claim 时刻写入的
  身份镜像（agent 行 = Agent 定义哈希、code 行 = code 文本 sha256）。
  node_runs 是 retention 永不删除的 audit trail，这层判定不受
  retention 窗口影响；本地 code 池执行（从不写请求行）的身份记录
  也落在这里。
- ``agent_execution_requests.agent_definition_hash``：dispatch 时刻的
  身份，请求行本身有独立消费者（claim 路由解析 join），只对**同一个
  最新 run** 作为 fallback——历史 Worker/Agent 作业（v85 前执行、请求
  行仍在 retention 窗内）由此保持零退化。绝不越过最新的不可证明 run
  去借用更老执行的身份，否则旧身份会冒充当前产物的执行证据。

skill 内容身份（codex 五轮 P1-A）与实现身份同读：段 1 取 run 行的
``skill_version``（v75 列，``ref@commit12``——dispatch 的
``SkillCheckout.version``）；段 2 取请求行 manifest 的
``skill_commit`` / ``skill_ref``（完整 sha 与 ref，mark_done trim 保留
该键）。两者按与实现身份相同的两段优先级合并。

``job_workflow_upgrade_impl``（服务层判定）经本门面读取某 job 各节点
最新 completed 执行的身份记录，与当前 published 身份比较。SQL 在数据
层，服务层不手写查询（BOUNDARY-DATA-001）。
"""

from __future__ import annotations

from collections.abc import Collection

from server.app.jobs.queries.connection import ConnectionQueriesMixin

#: 段 1/段 2 各自的记录形态（``ExecutionIdentityRecord`` 元组面）：
#: ``(kind, agent_definition_hash, skill_commit, skill_version)``。
#: kind 空串哨兵（node_runs 段）；skill_commit 空串 = 无完整 sha
#: （段 2 的 legacy manifest 或段 1 的无 skill 执行）；skill_version
#: 空串 = 无 ref@commit 记录。


class UpgradeImplIdentityQueriesMixin(ConnectionQueriesMixin):
    def latest_done_request_identities(
        self, job_id: str, node_keys: Collection[str]
    ) -> dict[str, tuple[str, str, str, str]]:
        """node_key → 最新完成执行的 ``(kind, hash, skill_commit, skill_version)``。

        每个 node_key 先取最新一条 completed ``node_runs``。该 run 的身份
        列非空时优先使用；为空时仅回落到 ``node_run_id`` 指向这条 run 的
        done 请求。请求 manifest 同时投影 ``skill_commit`` /
        ``skill_version``（trim 保留这些键）；即使 run 身份非空，也可从
        同一请求取得完整 skill commit，比 run 的 12 位 version 后缀更强。
        无 completed run → 该节点不在返回值里；最新 run 的两侧身份均空
        → 返回空 hash，由调用方按「不可证明」处理。
        """
        keys = sorted({str(key) for key in node_keys})
        if not keys:
            return {}
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                with latest_runs as (
                  select distinct on (node_key)
                         id, node_key, agent_definition_hash, skill_version
                  from node_runs
                  where job_id=%s
                    and node_key=any(%s)
                    and status='completed'
                  order by node_key, id desc
                )
                select n.node_key,
                       coalesce(r.kind, '') as kind,
                       coalesce(nullif(n.agent_definition_hash, ''),
                                r.agent_definition_hash, '') as implementation_hash,
                       coalesce(r.manifest_json::jsonb ->> 'skill_commit', '')
                         as skill_commit,
                       coalesce(nullif(n.skill_version, ''),
                                r.manifest_json::jsonb ->> 'skill_version', '')
                         as skill_version
                from latest_runs n
                left join lateral (
                  select kind, agent_definition_hash, manifest_json
                  from agent_execution_requests
                  where node_run_id=n.id and state='done'
                  order by finished_at desc nulls last, execution_id desc
                  limit 1
                ) r on true
                """,
                (job_id, keys),
            ).fetchall()
        return {
            str(row["node_key"]): (
                str(row["kind"] or ""),
                str(row["implementation_hash"] or ""),
                str(row["skill_commit"] or ""),
                str(row["skill_version"] or ""),
            )
            for row in rows
        }
