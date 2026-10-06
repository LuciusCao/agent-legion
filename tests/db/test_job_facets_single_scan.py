"""#957: job_facets 合并查询——结果与改动前的 5 次独立查询逐项相等。

total 与节点维度基数同一次扫描（active_node_key 走 FILTER 聚合），全部
facet 语句共用一个读连接；为空的 active_node_key 不再重复 count。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest

from server.app.jobs.queries.job_filtering import (
    _STATUS_BUCKET_SQL,
    JobListFilter,
    _where,
    count_jobs_filtered,
    job_facets,
)

_WS = "facet-ws"


def _reference_facets(job_db, workspace_id: str, f: JobListFilter) -> dict[str, Any]:
    """The pre-#957 algorithm, statement for statement (oracle)."""
    total = count_jobs_filtered(job_db, workspace_id, f)
    status_where, status_params = _where(workspace_id, replace(f, status=None))
    version_where, version_params = _where(
        workspace_id, replace(f, workflow_version=None, workflow_version_none=False)
    )
    node_where, node_params = _where(workspace_id, replace(f, active_node_key=None))
    with job_db._connect_read() as conn:
        status_counts = {
            str(r["bucket"]): int(r["cnt"])
            for r in conn.execute(
                f"select {_STATUS_BUCKET_SQL} as bucket, count(*) as cnt"
                f" from jobs{status_where} group by 1",
                status_params,
            )
        }
        version_counts = {
            r["workflow_version"]: int(r["cnt"])
            for r in conn.execute(
                f"select workflow_version, count(*) as cnt from jobs{version_where}"
                " group by workflow_version",
                version_params,
            )
        }
        node_counts = {
            r["active_node_key"]: int(r["cnt"])
            for r in conn.execute(
                "with picked as (select distinct on (job_id) job_id, node_key from job_nodes"
                " where status in ('running', 'failed')"
                " order by job_id, case when status = 'running' then 0 else 1 end, id)"
                " select p.node_key as active_node_key, count(*) as cnt"
                f" from picked p join jobs on jobs.id = p.job_id{node_where} group by 1",
                node_params,
            )
        }
    no_node = count_jobs_filtered(job_db, workspace_id, replace(f, active_node_key=None)) - sum(
        node_counts.values()
    )
    if no_node:
        node_counts[None] = no_node
    return {
        "total": total,
        "status_counts": status_counts,
        "version_counts": version_counts,
        "node_counts": node_counts,
    }


def _seed(job_db) -> None:
    # (job, status, version, paused, {node: status})
    jobs = [
        ("j1", "running", 1, 0, {"a": "running", "b": "pending"}),
        ("j2", "failed", 1, 0, {"a": "completed", "b": "failed"}),
        ("j3", "queued", 2, 0, {"a": "pending", "b": "pending"}),
        ("j4", "completed", 2, 1, {"a": "completed", "b": "completed"}),
        ("j5", "mystery", None, 0, {"a": "failed", "b": "running"}),
        ("j6", "running", None, 1, {"a": "running", "b": "failed"}),
    ]
    with job_db.connect() as conn:
        conn.execute("insert into workspaces(id, name) values (%s, 'F')", (_WS,))
        for job_id, status, version, paused, nodes in jobs:
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, status,"
                " workflow_version, execution_paused) values (%s, %s, 'q', %s, %s, %s, %s)",
                (job_id, _WS, job_id, status, version, paused),
            )
            for node_key, node_status in nodes.items():
                conn.execute(
                    "insert into job_nodes(job_id, node_key, status) values (%s, %s, %s)",
                    (job_id, node_key, node_status),
                )


_FILTERS = [
    JobListFilter(),
    JobListFilter(status="running"),
    JobListFilter(status="pending"),
    JobListFilter(workflow_version=2),
    JobListFilter(workflow_version_none=True),
    JobListFilter(active_node_key="a"),
    JobListFilter(active_node_key="b", paused=False),
    JobListFilter(active_node_key="", status="failed"),
    JobListFilter(active_node_key="a", workflow_version=1, status="running"),
    JobListFilter(active_node_key="zzz"),
]


@pytest.mark.parametrize("job_filter", _FILTERS, ids=repr)
def test_facets_match_pre_957_reference(job_db, job_filter: JobListFilter) -> None:
    _seed(job_db)
    assert job_facets(job_db, _WS, job_filter) == _reference_facets(job_db, _WS, job_filter)


@pytest.mark.parametrize("active_node_key", [None, "a"])
def test_facets_run_four_statements_on_one_connection(
    job_db, monkeypatch, active_node_key: str | None
) -> None:
    _seed(job_db)
    opened: list[list[str]] = []
    real = job_db._connect_read

    class _Conn:
        def __init__(self, conn: Any, log: list[str]) -> None:
            self._conn, self._log = conn, log

        def execute(self, sql: Any, *args: Any, **kwargs: Any) -> Any:
            self._log.append(str(sql))
            return self._conn.execute(sql, *args, **kwargs)

    @contextmanager
    def counting():
        log: list[str] = []
        opened.append(log)
        with real() as conn:
            yield _Conn(conn, log)

    monkeypatch.setattr(job_db, "_connect_read", counting)

    job_facets(job_db, _WS, JobListFilter(active_node_key=active_node_key))

    assert len(opened) == 1
    assert len(opened[0]) == 4
