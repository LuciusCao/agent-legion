"""failed-node-runs 有界查询（#713）。

最新一次 run 失败的判定从全 workspace ``row_number()`` 窗口改为反连接
（failed run 且同 (job, node) 无更新 run），并按 ``(finished_at desc,
node_run_id desc)`` keyset 分页。本文件钉住：

- 语义等价：同一数据集上，退役窗口查询（内嵌为对照 oracle）与新查询在
  各过滤组合下返回同一有序结果；
- 分页：逐页拼接 == 不分页结果（含 NULL finished_at 与 finished_at 并列）；
- 计划有界：规模数据集上 EXPLAIN 不含 WindowAgg、顶层 Limit，node_runs
  读行数远小于窗口版（窗口版须读完 workspace 全部 run）。
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

import pytest

from server.app.jobs.queries import JobQueries
from server.app.jobs.queries.failed_node_runs_sql import latest_failed_runs_sql
from server.app.jobs.queries.failed_run_cursor import (
    format_failed_run_cursor,
    parse_failed_run_cursor,
)
from server.app.services.failed_node_runs import FailedNodeRunQueryService

# 退役的 #713 前写法：窗口函数扫 workspace 全部 node_runs 再过滤。
_WINDOW_ORACLE_SQL = """
select latest.node_run_id, latest.job_id, latest.node_key, latest.failure_category,
       latest.failure_detail, latest.error_message, latest.finished_at
from (
  select node_runs.id as node_run_id, node_runs.job_id, node_runs.node_key,
         node_runs.status, node_runs.failure_category, node_runs.failure_detail,
         node_runs.error_message, node_runs.finished_at,
         row_number() over (
           partition by node_runs.job_id, node_runs.node_key order by node_runs.id desc
         ) as rn
  from node_runs join jobs on jobs.id = node_runs.job_id
  where jobs.workspace_id = %s
) latest
where latest.rn = 1 and latest.status = 'failed' {extra}
order by latest.finished_at desc, latest.node_run_id desc
"""


def _seed_workspaces(job_db: JobQueries) -> None:
    with job_db.connect() as conn:
        conn.execute("insert into workspaces(id, name) values ('ws-a', 'a'), ('ws-b', 'b')")
        conn.execute("commit")


def _seed_random_runs(job_db: JobQueries, *, seed: int) -> None:
    """Mixed history: several runs per (job, node), statuses/categories mixed,
    some NULL finished_at, and deliberate finished_at ties across jobs."""
    rng = random.Random(seed)
    _seed_workspaces(job_db)
    with job_db.connect() as conn:
        for index in range(30):
            workspace = "ws-a" if index % 3 else "ws-b"
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, title, status)"
                " values (%s, %s, 's', %s, 't', 'failed')",
                (f"job-{index:02d}", workspace, f"s{index}"),
            )
        for _ in range(400):
            job = f"job-{rng.randrange(30):02d}"
            status = rng.choice(["failed", "failed", "completed", "running"])
            finished = rng.choice([None, *range(12)])  # 少量取值 → 大量并列
            conn.execute(
                "insert into node_runs(job_id, node_key, status, failure_category,"
                " failure_detail, error_message, finished_at)"
                " values (%s, %s, %s, %s, %s, 'boom',"
                " case when %s::int is null then null"
                "   else timestamptz '2026-01-01' + make_interval(mins => %s::int) end)",
                (
                    job,
                    rng.choice(["n1", "n2", "n3"]),
                    status,
                    rng.choice(["technical", "business", ""]),
                    rng.choice(["provider_stream", "review_rejected", ""]),
                    finished,
                    finished,
                ),
            )
        conn.execute("commit")


def _oracle(job_db: JobQueries, workspace_id: str, **filters: Any) -> list[dict[str, Any]]:
    extra = ""
    params: list[Any] = [workspace_id]
    for column in ("category", "detail"):
        if filters.get(column):
            extra += f" and latest.failure_{column} = %s"
            params.append(filters[column])
    if filters.get("node_key"):
        extra += " and latest.node_key = %s"
        params.append(filters["node_key"])
    if filters.get("job_ids"):
        extra += " and latest.job_id = any(%s)"
        params.append(list(filters["job_ids"]))
    with job_db.connect() as conn:
        rows = conn.execute(_WINDOW_ORACLE_SQL.format(extra=extra), params).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize("seed", [7, 713])
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"category": "technical"},
        {"detail": "review_rejected"},
        {"category": "business", "detail": "provider_stream"},
        {"node_key": "n2"},
        {"job_ids": ["job-01", "job-04", "job-07", "job-29"]},
    ],
)
def test_anti_join_matches_retired_window_query(
    job_db: JobQueries, seed: int, filters: dict[str, Any]
) -> None:
    _seed_random_runs(job_db, seed=seed)
    matched = 0
    for workspace_id in ("ws-a", "ws-b"):
        expected = _oracle(job_db, workspace_id, **filters)
        actual = job_db.list_failed_node_runs(workspace_id, **filters)
        assert actual == expected
        matched += len(expected)
    assert matched, "数据集应产生非空结果，否则等价断言失去意义"


def test_failed_job_ids_match_window_query(job_db: JobQueries) -> None:
    _seed_random_runs(job_db, seed=11)
    expected = {str(row["job_id"]) for row in _oracle(job_db, "ws-a", category="technical")}
    actual = job_db.list_failed_job_ids("ws-a", category="technical", limit=1000)
    assert sorted(actual) == sorted(expected)
    assert len(job_db.list_failed_job_ids("ws-a", category="technical", limit=2)) == 2


@pytest.mark.parametrize("page_size", [1, 3, 7])
def test_keyset_pages_concatenate_to_unpaged_result(job_db: JobQueries, page_size: int) -> None:
    _seed_random_runs(job_db, seed=29)
    service = FailedNodeRunQueryService(job_db)
    unpaged = job_db.list_failed_node_runs("ws-a")
    assert any(row["finished_at"] is None for row in unpaged), "需覆盖 NULL finished_at"
    collected: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(len(unpaged) + 2):
        page, cursor = service.list_failed_node_runs_page("ws-a", limit=page_size, cursor=cursor)
        assert len(page) <= page_size
        collected.extend(page)
        if cursor is None:
            break
    assert cursor is None
    assert collected == unpaged


def test_cursor_round_trips_null_and_timestamp(job_db: JobQueries) -> None:
    _seed_random_runs(job_db, seed=3)
    rows = job_db.list_failed_node_runs("ws-a")
    for row in rows:
        parsed = parse_failed_run_cursor(format_failed_run_cursor(row))
        finished = row["finished_at"]
        expected = None if finished is None else datetime.fromisoformat(str(finished))
        assert parsed == (expected, row["node_run_id"])


@pytest.mark.parametrize("bad", ["nope", "|", "|x", "2026-01-01 00:00:00|", "garbage|12", "|١٢"])
def test_malformed_cursor_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_failed_run_cursor(bad)


# --- 规模：计划有界（#713 验收「大表规模耗时可测」） -------------------------

_SCALE_JOBS = 4000
_SCALE_NODES = 8


def _seed_scale(job_db: JobQueries) -> int:
    """两个 workspace，每 (job, node) 两次 run，约 1/5 首次失败、少量最新失败。"""
    _seed_workspaces(job_db)
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, title, status)"
            " select 'j' || g, case when g %% 2 = 0 then 'ws-a' else 'ws-b' end,"
            " 's', 's' || g, 't', 'completed' from generate_series(1, %s) g",
            (_SCALE_JOBS,),
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, finished_at, failure_category)"
            " select 'j' || g, 'n' || n,"
            "  case when r = 2 and (g * 7 + n) %% 33 = 0 then 'failed'"
            "       when r = 1 and (g + n) %% 5 = 0 then 'failed' else 'completed' end,"
            "  timestamptz '2026-01-01' + make_interval(secs => g * 10 + n + r),"
            "  case when (g + n) %% 2 = 0 then 'technical' else 'business' end"
            " from generate_series(1, %s) g, generate_series(1, %s) n,"
            "  generate_series(1, 2) r order by g, n, r",
            (_SCALE_JOBS, _SCALE_NODES),
        )
        conn.execute("commit")
        conn.execute("analyze node_runs")
        conn.execute("analyze jobs")
        conn.execute("commit")
        row = conn.execute(
            "select count(*) as n from node_runs join jobs on jobs.id = node_runs.job_id"
            " where jobs.workspace_id = 'ws-a'"
        ).fetchone()
    return int(row["n"])


def _plan(job_db: JobQueries, sql: str, params: list[Any]) -> dict[str, Any]:
    with job_db.connect() as conn:
        row = conn.execute(f"explain (analyze, format json) {sql}", params).fetchone()
    document = next(iter(row.values()))
    if isinstance(document, str):
        document = json.loads(document)
    return document[0]["Plan"]


def _walk(plan: dict[str, Any]):
    yield plan
    for child in plan.get("Plans", []):
        yield from _walk(child)


def _node_runs_rows_read(plan: dict[str, Any]) -> int:
    """Rows produced by the outer (``latest``) node_runs scan — the probe
    side ``newer`` is per-job and bounded by the job's own history."""
    total = 0
    for node in _walk(plan):
        if node.get("Relation Name") == "node_runs" and node.get("Alias") != "newer":
            total += int(node.get("Actual Rows", 0)) * int(node.get("Actual Loops", 1))
            total += int(node.get("Rows Removed by Filter", 0))
    return total


def test_page_query_plan_is_bounded_at_scale(job_db: JobQueries) -> None:
    workspace_runs = _seed_scale(job_db)
    page = 50
    latest_sql, params = latest_failed_runs_sql("ws-a")
    new_plan = _plan(
        job_db,
        f"select latest.id {latest_sql} order by latest.finished_at desc, latest.id desc limit %s",
        [*params, page + 1],
    )
    old_plan = _plan(job_db, _WINDOW_ORACLE_SQL.format(extra="") + " limit %s", ["ws-a", page + 1])

    node_types = {node["Node Type"] for node in _walk(new_plan)}
    assert new_plan["Node Type"] == "Limit"
    assert "WindowAgg" not in node_types
    assert "Sort" not in node_types, "应沿 (status, finished_at, id) 索引倒序取页，不全量排序"
    # 窗口版必须读完 workspace 的全部 run；新查询只读到凑满一页为止。
    assert _node_runs_rows_read(old_plan) >= workspace_runs
    assert _node_runs_rows_read(new_plan) * 10 < workspace_runs
