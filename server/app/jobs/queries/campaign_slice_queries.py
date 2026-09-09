"""Campaign queries split from queries/campaigns.py at its budget ceiling
(#209 ratchet — the sibling-module precedent of studio_publish_requests):
the campaign row lifecycle stays in campaigns.py; this mixin carries the
reads beyond the row itself — the feeder's keyset slicer (PR-B) and the
submit-mode run-overview aggregation (PR-C).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Per-run job-count summary for the campaign detail view: rides the
# trigger-maintained counter table (DB-RUN-JOB-STATUS-COUNTS-001) instead
# of a group-by over each run's whole jobs slice. 先在子查询里截取最新
# limit 条 run（外层 LIMIT 只做 top-N，不约束聚合前的扫描/连接范围——
# PostgreSQL 仍会读完该 campaign 的全部历史 run 再逐条聚合），外层只对
# 这 limit 条做计数表连接与求和，详情请求的成本不随历史批次数增长。
_CAMPAIGN_RUN_OVERVIEW_SQL = """
select r.id, r.status, r.created_count,
       coalesce(sum(c.cnt), 0) as job_count
from (
    select id, status, created_count, created_at
    from runs
    where campaign_id = %s
    order by created_at desc, id desc
    limit %s
) r
left join run_job_status_counts c on c.run_id = r.id and c.cnt <> 0
group by r.id, r.status, r.created_count, r.created_at
order by r.created_at desc, r.id desc
"""


class CampaignSliceQueriesMixin(ConnectionQueriesMixin):
    def list_campaign_runs_overview(
        self, campaign_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Submit-mode run overview for the campaign detail endpoint (PR-C).

        骑 idx_runs_campaign 部分索引；最新 limit 条子查询约束连接+聚合
        范围（PR #559 二轮：外层 LIMIT 只做 top-N 不约束扫描，Postgres
        原本逐条聚合全部历史）。job 数取计数表，不随 campaign 体量增长。
        手动提交完成的 run 仍在此出现——确定性 run id 使批次与手动运行
        同行。"""
        with self._connect_read() as conn:
            rows = conn.execute(_CAMPAIGN_RUN_OVERVIEW_SQL, (campaign_id, limit)).fetchall()
        return [
            {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "created_count": int(row["created_count"]),
                "job_count": int(row["job_count"]),
            }
            for row in rows
        ]

    def list_campaign_filter_job_ids_page(
        self,
        workspace_id: str,
        job_filter: Any,
        limit: int,
        cursor: str | None,
        exclude_ids: Collection[str] = (),
    ) -> tuple[list[str], str | None]:
        """One keyset page of job ids matching ``job_filter``, newest first.

        feeder 的 filter 形态取片器（§1.4）：resolver 的 keyset 语义
        （created_at|id 复合游标）以 JobQueries 方法表达（BOUNDARY-DATA-001，
        campaign worker 不自开连接）。exclude_ids 在 SQL 内排除而非 feeder
        侧过滤——页保持满尺寸、limit+1 前瞻的游标算术不受排除项影响。
        """
        from server.app.jobs.queries.job_filtering import filter_clauses

        clauses, filter_params = filter_clauses(job_filter)
        where = f" where workspace_id = %s{''.join(f' and {c}' for c in clauses)}"
        params: list[Any] = [workspace_id, *filter_params]
        excluded = [value for value in dict.fromkeys(exclude_ids) if value]
        if excluded:
            where += " and id != all(%s)"
            params.append(excluded)
        if cursor:
            created_at, job_id = cursor.split("|", 1)
            where += " and (created_at < %s or (created_at = %s and id < %s))"
            params.extend([created_at, created_at, job_id])
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select id, created_at from jobs{where}"
                " order by created_at desc, id desc limit %s",
                (*params, limit + 1),
            ).fetchall()
        page = [(str(row["id"]), str(row["created_at"])) for row in rows]
        if len(page) <= limit:
            return [job_id for job_id, _created_at in page], None
        last_id, last_created_at = page[limit - 1]
        return [job_id for job_id, _created_at in page[:limit]], f"{last_created_at}|{last_id}"
